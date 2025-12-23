import os
import time
import json
import re
import requests
import arxiv
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
import google.generativeai as genai
import secrets
from datetime import datetime, timedelta
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    send_from_directory,
    flash,
)
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    current_user,
    logout_user,
    UserMixin,
)
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = "devsync-secret"

# --- 1. CONFIGURATION ---
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url or "sqlite:///devsync.db"
app.config["UPLOAD_FOLDER"] = "/tmp"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# --- 2. ADVANCED AI ENGINE INTEGRATION ---

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)


def get_active_model():
    """Finds a working Gemini model dynamically."""
    try:
        if not api_key:
            return None
        # 1. Prefer Flash (Faster/Cheaper)
        for m in genai.list_models():
            if (
                "generateContent" in m.supported_generation_methods
                and "flash" in m.name.lower()
            ):
                print(f"✅ Using Model: {m.name}")
                return genai.GenerativeModel(m.name)
        # 2. Fallback to Pro
        for m in genai.list_models():
            if (
                "generateContent" in m.supported_generation_methods
                and "pro" in m.name.lower()
            ):
                return genai.GenerativeModel(m.name)
        return genai.GenerativeModel("gemini-1.5-flash")
    except Exception as e:
        print(f"Model Error: {e}")
        return genai.GenerativeModel("gemini-pro")


active_model = get_active_model()

# --- HELPER FUNCTIONS FROM AI_ENGINE.PY ---


def clean_json_text(text):
    """Clean markdown and extra text from AI response to extract valid JSON."""
    try:
        text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != -1:
            text = text[start:end]
        return text
    except:
        return text


def is_valid_url(url):
    if not url:
        return False
    academic_signals = [
        ".edu",
        ".ac.",
        ".org",
        "university",
        "institute",
        "lab",
        "research",
        "faculty",
        "prof",
    ]
    if not any(signal in url.lower() for signal in academic_signals):
        return False
    blacklist = ["youtube", "facebook", "twitter", "linkedin", "instagram", "tiktok"]
    for bad in blacklist:
        if bad in url.lower():
            return False
    return True


def scrape_website_text(url):
    if not url:
        return ""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code != 200:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for script in soup(["script", "style", "nav", "footer"]):
            script.extract()
        text = soup.get_text()
        return " ".join(text.split())[:6000]  # Limit context size
    except:
        return ""


def find_lab_url(professor_name):
    """Finds the professor's lab website."""
    if not professor_name:
        return None
    query = f"{professor_name} research lab official website"
    ddgs = DDGS()
    try:
        results = ddgs.text(query, max_results=3)
        if results:
            for res in results:
                if is_valid_url(res["href"]):
                    return res["href"]
    except:
        pass
    return None


def extract_arxiv_id(url):
    """Extracts ArXiv ID from a URL."""
    match = re.search(r"arxiv.org/(?:abs|pdf)/(\d+\.\d+)", url)
    if match:
        return match.group(1)
    return None


def get_paper_metadata(query):
    print(f"🔍 Fetching metadata for: {query}")

    # 0. Check if input is an ArXiv URL and extract ID
    arxiv_id = extract_arxiv_id(query)
    if arxiv_id:
        print(f"✅ Detected ArXiv ID: {arxiv_id}")
        query = arxiv_id  # Search by ID instead of URL for better results

    # 1. Try Semantic Scholar
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {"query": query, "limit": 1, "fields": "title,abstract,authors,venue"}
        res = requests.get(url, params=params, timeout=4)
        if res.status_code == 200:
            data = res.json()
            if data.get("data") and len(data["data"]) > 0:
                print("✅ Found via Semantic Scholar")
                return data["data"][0]
    except Exception as e:
        print(f"⚠️ Semantic Scholar Error: {e}")

    # 2. Fallback to ArXiv API
    try:
        # If query is an ID, use id_list, else use search_query
        if arxiv_id:
            url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
        else:
            safe_query = query.replace(" ", "+")
            url = f"http://export.arxiv.org/api/query?search_query=all:{safe_query}&start=0&max_results=1"

        data = requests.get(url).content
        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)

        if entry:
            print("✅ Found via ArXiv API")
            title = entry.find("atom:title", ns).text.strip()
            summary = entry.find("atom:summary", ns).text.strip()
            authors = [
                {"name": author.find("atom:name", ns).text}
                for author in entry.findall("atom:author", ns)
            ]

            # Sanity check: If summary is extremely short or empty, consider it a failure
            if len(summary) < 50:
                return {
                    "title": query,
                    "abstract": "Abstract content unavailable.",
                    "authors": [],
                }

            return {
                "title": title,
                "abstract": summary,
                "authors": authors,
                "venue": "ArXiv",
            }
    except Exception as e:
        print(f"⚠️ ArXiv Error: {e}")

    print("❌ Metadata lookup failed.")
    return {
        "title": query,
        "abstract": "No abstract found. Please provide text content directly.",
        "authors": [],
    }


# --- 3. DATABASE MODELS ---
db = SQLAlchemy(app)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    full_name = db.Column(db.String(100))
    qualification = db.Column(db.String(100))
    college = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    resume_file = db.Column(db.String(200))
    reset_token = db.Column(db.String(100), nullable=True)
    token_expiry = db.Column(db.DateTime, nullable=True)
    internships = db.relationship("Internship", backref="author", lazy=True)
    applications = db.relationship("Application", backref="student", lazy=True)


class Internship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    domain = db.Column(db.String(100))
    description = db.Column(db.Text)
    type = db.Column(db.String(50))
    pdf_link = db.Column(db.String(500))
    vacancies = db.Column(db.String(50), default="Open")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    applications = db.relationship("Application", backref="internship", lazy=True)


class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    internship_id = db.Column(db.Integer, db.ForeignKey("internship.id"), nullable=True)
    cover_letter = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="Pending")


# --- 4. LOGIN MANAGER ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "index"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- 5. ROUTES ---


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(
            "/professor" if current_user.role == "Professor" else "/student"
        )
    return render_template("index.html")


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email")
    password = request.form.get("password")
    user = User.query.filter_by(email=email).first()
    if user and user.password == password:
        login_user(user)
        flash("Welcome back, " + user.full_name.split()[0] + "!", "success")
        return redirect("/professor" if user.role == "Professor" else "/student")
    flash("Invalid email or password.", "error")
    return redirect("/")


@app.route("/signup", methods=["POST"])
def signup():
    email = request.form.get("email")
    if User.query.filter_by(email=email).first():
        flash("Account already exists!", "warning")
        return redirect("/")
    new_user = User(
        email=email,
        password=request.form.get("password"),
        role=request.form.get("role"),
        full_name=request.form.get("full_name"),
    )
    db.session.add(new_user)
    db.session.commit()
    login_user(new_user)
    return redirect("/setup")


@app.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    if request.method == "POST":
        current_user.full_name = request.form.get("full_name")
        current_user.qualification = request.form.get("qualification")
        current_user.college = request.form.get("college")
        current_user.phone = request.form.get("phone")
        if "research_domain" in request.form:
            current_user.research_domain = request.form.get("research_domain")
        if "resume" in request.files:
            file = request.files["resume"]
            if file.filename != "":
                filename = secure_filename(f"{current_user.id}_{file.filename}")
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                current_user.resume_file = filename
        db.session.commit()
        return redirect(
            "/professor" if current_user.role == "Professor" else "/student"
        )
    return render_template("setup.html")


@app.route("/submit_application", methods=["POST"])
@login_required
def submit_application():
    file = request.files.get("resume")
    if file:
        filename = secure_filename(f"{current_user.id}_{file.filename}")
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        current_user.resume_file = filename
    cover_text = request.form.get("cover_letter")
    existing_app = Application.query.filter_by(
        student_id=current_user.id, internship_id=None
    ).first()
    if existing_app:
        existing_app.cover_letter = cover_text
    else:
        new_app = Application(
            student_id=current_user.id, internship_id=None, cover_letter=cover_text
        )
        db.session.add(new_app)
    db.session.commit()
    return jsonify({"status": "success", "message": "Application Sent Successfully!"})


@app.route("/download_resume/<filename>")
@login_required
def download_resume(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/student")
@login_required
def student():
    if current_user.role == "Professor":
        return redirect("/professor")
    apps = Application.query.filter_by(student_id=current_user.id).all()
    return render_template("student.html", applications=apps)


@app.route("/papers")
@login_required
def papers():
    internships = Internship.query.all()
    my_apps = [
        app.internship_id
        for app in Application.query.filter_by(student_id=current_user.id).all()
    ]
    return render_template("papers.html", internships=internships, my_apps=my_apps)


@app.route("/my_applications")
@login_required
def my_applications():
    if current_user.role != "Student":
        return redirect("/")
    apps = Application.query.filter_by(student_id=current_user.id).all()
    return render_template("my_applications.html", applications=apps)


# --- THE UPGRADED OPTIMIZE ROUTE ---
@app.route("/optimize", methods=["POST"])
@login_required
def optimize():
    data = request.json
    content_input = data.get("content") or data.get("url") or ""
    professor_name = data.get("professor_name", "")

    # 1. Gather Metadata (Paper Info + Lab Website)
    paper_data = get_paper_metadata(content_input)

    # Check if we ACTUALLY got data
    is_valid_paper = paper_data.get(
        "abstract"
    ) and "No abstract found" not in paper_data.get("abstract")

    # If professor_name is missing, try to guess from paper authors (Last author usually PI)
    if not professor_name and paper_data.get("authors"):
        try:
            professor_name = paper_data["authors"][-1]["name"]
            print(f"🤖 Auto-detected Professor/PI: {professor_name}")
        except:
            pass

    lab_text = ""
    lab_url = ""

    if professor_name:
        lab_url = find_lab_url(professor_name)
        if lab_url:
            lab_text = scrape_website_text(lab_url)

    # 2. Build the Advanced Prompt
    prompt = f"""
    Act as a Research Consultant.
    
    PAPER TITLE: {paper_data.get("title")}
    PAPER ABSTRACT: {paper_data.get("abstract")}
    
    PROFESSOR: {professor_name if professor_name else "Unknown"}
    LAB WEBSITE CONTEXT: {lab_text if lab_text else "Not available"}
    
    STUDENT: {current_user.full_name}, {current_user.qualification} at {current_user.college}
    
    IMPORTANT: 
    1. If the PAPER ABSTRACT above indicates 'No abstract found' or is missing, DO NOT Hallucinate a summary. 
       Instead, return a summary stating "Could not extract paper details. Please verify the URL." 
       and set Skills to "Manual Review".
    2. If the abstract IS available, generate a highly specific cold email.
    
    Goal: Write a highly specific cold email for an internship.
    
    OUTPUT JSON ONLY (No Markdown):
    {{
        "summary": "2 sentence summary of the paper/topic",
        "skills": ["Skill1", "Skill2", "Skill3"],
        "citation_score": "Impact level (e.g. High, Medium, or Citation Count)",
        "vacancies": "Potential Role (e.g. Research Assistant)",
        "applicants": "Estimate (e.g. High, Medium)",
        "application": "The cold email body. Address 'Dear Prof. [Last Name]' using the PROFESSOR name provided above. Mention specific details from the abstract or lab context."
    }}
    """

    try:
        # Use the global active model
        response = active_model.generate_content(
            prompt, generation_config={"response_mime_type": "application/json"}
        )

        # Robust Parsing
        try:
            result = json.loads(response.text)
        except:
            # Fallback for older models that don't enforce JSON strictly
            cleaned_text = clean_json_text(response.text)
            result = json.loads(cleaned_text)

        # Map to Frontend Expected Format
        return jsonify(
            {
                "analysis": {
                    "summary": result.get("summary", "Analysis unavailable."),
                    "skills": result.get("skills", ["General Research"]),
                    "citation_score": result.get("citation_score", "N/A"),
                    "vacancies": result.get("vacancies", "Open"),
                    "applicants": result.get("applicants", "Many"),
                },
                "application": result.get("application", "Error generating email."),
            }
        )

    except Exception as e:
        print("AI AGENT ERROR:", e)
        # Fallback response so frontend doesn't crash
        return jsonify(
            {
                "analysis": {
                    "summary": "Could not analyze content deeper.",
                    "skills": ["Manual Review"],
                    "citation_score": "N/A",
                    "vacancies": "Check Listing",
                    "applicants": "Unknown",
                },
                "application": f"Dear Prof. {professor_name.split()[-1] if professor_name else ''},\n\nI am writing to express my interest in your research...",
            }
        )


@app.route("/apply/<int:internship_id>", methods=["POST"])
@login_required
def apply_for_internship(internship_id):
    existing = Application.query.filter_by(
        student_id=current_user.id, internship_id=internship_id
    ).first()
    cover_letter = request.form.get("cover_letter", "Interested in this role.")

    if not existing:
        new_app = Application(
            student_id=current_user.id,
            internship_id=internship_id,
            cover_letter=cover_letter,
            status="Pending",
        )
        db.session.add(new_app)
        db.session.commit()

    file = request.files.get("resume")
    if file:
        filename = secure_filename(f"{current_user.id}_{file.filename}")
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        current_user.resume_file = filename
        db.session.commit()

    return redirect("/my_applications")


@app.route("/cold_applications")
@login_required
def cold_applications():
    if current_user.role != "Professor":
        return "Unauthorized"
    applications = Application.query.filter_by(internship_id=None).all()
    return render_template("applicants.html", applications=applications)


@app.route("/professor")
@login_required
def professor():
    if current_user.role != "Professor":
        return redirect("/student")
    my_internships = Internship.query.filter_by(user_id=current_user.id).all()
    return render_template("professor.html", internships=my_internships)


@app.route("/post_internship", methods=["POST"])
@login_required
def post_internship():
    if current_user.role != "Professor":
        return "Unauthorized"
    new_internship = Internship(
        title=request.form.get("title"),
        domain=request.form.get("domain"),
        description=request.form.get("description"),
        type=request.form.get("type"),
        user_id=current_user.id,
        vacancies=request.form.get("vacancies") or "Open",
    )
    db.session.add(new_internship)
    db.session.commit()
    return redirect("/professor")


@app.route("/view_applicants/<int:id>")
@login_required
def view_applicants(id):
    internship = Internship.query.get(id)
    if internship.user_id != current_user.id:
        return "Unauthorized"
    applications = Application.query.filter_by(internship_id=id).all()
    return render_template(
        "applicants.html", internship=internship, applications=applications
    )


@app.route("/all_applications")
@login_required
def all_applications():
    if current_user.role != "Professor":
        return "Unauthorized"
    my_internships = Internship.query.filter_by(user_id=current_user.id).all()
    my_internship_ids = [i.id for i in my_internships]
    applications = Application.query.filter(
        Application.internship_id.in_(my_internship_ids)
    ).all()
    return render_template("applicants.html", applications=applications)


@app.route("/accept_applicant/<int:app_id>")
@login_required
def accept_applicant(app_id):
    if current_user.role != "Professor":
        return "Unauthorized"
    application = Application.query.get(app_id)
    if application:
        application.status = "Selected"
        db.session.commit()
        flash(f"Student {application.student.full_name} has been selected!", "success")
    return redirect(request.referrer or "/professor")


@app.route("/generate_feed")
@login_required
def generate_feed():
    if current_user.role != "Professor":
        return "Unauthorized"

    try:
        client = arxiv.Client()
        search = arxiv.Search(
            query="artificial intelligence",
            max_results=3,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )

        count = 0
        for result in client.results(search):
            if Internship.query.filter_by(title=result.title).first():
                continue

            try:
                prompt = f"Summarize this research abstract into a 2-sentence internship opportunity description: {result.summary}"
                response = active_model.generate_content(prompt)
                ai_description = response.text

                # --- NEW ADDITION: PAUSE FOR 5 SECONDS ---
                print("Sleeping for 5s to respect API quota...")
                time.sleep(5)
                # -----------------------------------------

            except Exception as e:
                print(f"AI Summary failed: {e}")
                ai_description = result.summary[:300] + "..."

            new_internship = Internship(
                title=result.title,
                domain="AI & Machine Learning",
                description=ai_description,
                type="Remote Research",
                user_id=current_user.id,
                pdf_link=result.pdf_url,
                vacancies="2",
            )
            db.session.add(new_internship)
            count += 1

        db.session.commit()
        if count > 0:
            flash(f"Successfully added {count} new papers!", "success")
        else:
            flash("No new unique papers found.", "warning")

    except Exception as e:
        print(f"Feed Error: {e}")
        flash("Error generating feed. Check logs.", "error")

    return redirect("/professor")


@app.route("/contact")
@login_required
def contact():
    return render_template("contact.html")


@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email")
        user = User.query.filter_by(email=email).first()
        if user:
            token = secrets.token_hex(16)
            user.reset_token = token
            user.token_expiry = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()
            reset_link = url_for("reset_password", token=token, _external=True)
            print(f"\n\n=== PASSWORD RESET: {reset_link} ===\n\n")
            flash("Reset link sent to your email (Check Terminal)!", "success")
        else:
            flash("Email not found.", "error")
        return redirect("/forgot_password")
    return render_template("forgot_password.html")


@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.token_expiry or user.token_expiry < datetime.utcnow():
        flash("Invalid or expired token.", "error")
        return redirect("/")
    if request.method == "POST":
        user.password = request.form.get("password")
        user.reset_token = None
        user.token_expiry = None
        db.session.commit()
        flash("Password reset successfully! Please login.", "success")
        return redirect("/")
    return render_template("reset_password.html", token=token)


@app.route("/logout")
def logout():
    logout_user()
    return redirect("/")


# --- TEMPORARY ROUTE TO INITIALIZE DATABASE ---
# --- TEMPORARY ROUTE TO INITIALIZE DATABASE ---


@app.route("/seed_database")
def seed_database():
    # 1. Create Tables
    with app.app_context():
        db.create_all()

        # 2. Create Professor Account
        if not User.query.filter_by(email="prof@mit.edu").first():
            prof = User(
                email="prof@mit.edu",
                password="123",
                role="Professor",
                full_name="Dr. Elara Vance",
            )
            db.session.add(prof)
            db.session.commit()

        # 3. Add Research Papers
        prof = User.query.filter_by(email="prof@mit.edu").first()

        papers = [
            {
                "title": "Attention Is All You Need",
                "domain": "Deep Learning",
                "type": "Remote",
                "desc": "The seminal paper introducing the Transformer architecture, the foundation of modern LLMs like GPT.",
            },
            {
                "title": "YOLOv8: Real-Time Detection",
                "domain": "Computer Vision",
                "type": "Onsite",
                "desc": "State-of-the-art real-time object detection model offering SOTA performance on COCO dataset.",
            },
            {
                "title": "BERT: Pre-training of Deep Transformers",
                "domain": "NLP",
                "type": "Hybrid",
                "desc": "Bidirectional Encoder Representations from Transformers (BERT) revolutionized NLP tasks.",
            },
            {
                "title": "Llama 2: Open Foundation Models",
                "domain": "Generative AI",
                "type": "Remote",
                "desc": "A collection of open-source pretrained and fine-tuned large language models (LLMs).",
            },
        ]

        for p in papers:
            if not Internship.query.filter_by(title=p["title"]).first():
                new_paper = Internship(
                    title=p["title"],
                    domain=p["domain"],
                    type=p["type"],
                    description=p["desc"],
                    # REMOVED: required_skills line to fix the error
                    user_id=prof.id,
                )
                db.session.add(new_paper)

        db.session.commit()
        return "✅ Database initialized! <a href='/'>Go to Home</a>"


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
