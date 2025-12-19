import os
import google.generativeai as genai
import arxiv
import secrets
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, flash
from flask_login import LoginManager, login_user, login_required, current_user, logout_user, UserMixin
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

app = Flask(__name__)
app.config['SECRET_KEY'] = 'devsync-secret'

# --- 1. CONFIGURATION (Render Compatible) ---
# Check for Render's database URL, otherwise use local SQLite
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///devsync.db'

# NOTE: Render Free Tier deletes local files (resumes) on restart.
# To persist files, you need a paid "Disk" or Cloud storage (S3/Cloudinary).
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- 2. CONFIGURE GEMINI API ---
genai.configure(api_key = os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('models/gemini-flash-latest')

# --- 3. DATABASE CONFIGURATION & MODELS ---
db = SQLAlchemy(app)

# Define User Model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50), nullable=False) # 'Student' or 'Professor'
    full_name = db.Column(db.String(100))
    qualification = db.Column(db.String(100))
    college = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    resume_file = db.Column(db.String(200)) # Stores filename
    
    # --- NEW COLUMNS FOR PASSWORD RESET ---
    reset_token = db.Column(db.String(100), nullable=True)
    token_expiry = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    internships = db.relationship('Internship', backref='author', lazy=True)
    applications = db.relationship('Application', backref='student', lazy=True)

# Define Internship Model
class Internship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    domain = db.Column(db.String(100))
    description = db.Column(db.Text)
    type = db.Column(db.String(50)) # Remote, Onsite
    pdf_link = db.Column(db.String(500)) # For generated papers
    # [NEW COLUMN] For Professor to set vacancies manually
    vacancies = db.Column(db.String(50), default="Open") 
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    applications = db.relationship('Application', backref='internship', lazy=True)

# Define Application Model
class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    internship_id = db.Column(db.Integer, db.ForeignKey('internship.id'), nullable=True) # Make nullable for external papers
    cover_letter = db.Column(db.Text, nullable=True) 
    status = db.Column(db.String(50), default="Pending")

# --- 4. LOGIN MANAGER ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'index'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 5. ROUTES ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect('/professor' if current_user.role == 'Professor' else '/student')
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    password = request.form.get('password')
    user = User.query.filter_by(email=email).first()
    
    if user and user.password == password:
        login_user(user)
        flash('Welcome back, ' + user.full_name.split()[0] + '!', 'success')
        return redirect('/professor' if user.role == 'Professor' else '/student')
    
    flash('Invalid email or password. Please try again.', 'error')
    return redirect('/')

@app.route('/signup', methods=['POST'])
def signup():
    email = request.form.get('email')
    if User.query.filter_by(email=email).first(): 
        flash('Account already exists! Please log in.', 'warning')
        return redirect('/') 
    
    new_user = User(email=email, password=request.form.get('password'), 
                    role=request.form.get('role'), full_name=request.form.get('full_name'))
    db.session.add(new_user)
    db.session.commit()
    login_user(new_user)
    return redirect('/setup')

@app.route('/setup', methods=['GET', 'POST'])
@login_required
def setup():
    if request.method == 'POST':
        current_user.full_name = request.form.get('full_name')
        current_user.qualification = request.form.get('qualification')
        current_user.college = request.form.get('college')
        current_user.phone = request.form.get('phone')
        # Check if research_domain is in form (for professors)
        if 'research_domain' in request.form:
             current_user.research_domain = request.form.get('research_domain')
        
        # Handle Resume Upload
        if 'resume' in request.files:
            file = request.files['resume']
            if file.filename != '':
                filename = secure_filename(f"{current_user.id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                current_user.resume_file = filename

        db.session.commit()
        return redirect('/professor' if current_user.role == 'Professor' else '/student')
    return render_template('setup.html')

@app.route('/submit_application', methods=['POST'])
@login_required
def submit_application():
    # 1. Handle File Upload
    file = request.files.get('resume')
    if file:
        filename = secure_filename(f"{current_user.id}_{file.filename}")
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        current_user.resume_file = filename
    
    # 2. Handle Cover Letter Text
    cover_text = request.form.get('cover_letter')
    
    # 3. Create an Application Record
    existing_app = Application.query.filter_by(student_id=current_user.id, internship_id=None).first()
    
    if existing_app:
        existing_app.cover_letter = cover_text # Update existing
    else:
        new_app = Application(
            student_id=current_user.id,
            internship_id=None, # None means it's an external/general application
            cover_letter=cover_text
        )
        db.session.add(new_app)
        
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Application Sent Successfully!'})

@app.route('/download_resume/<filename>')
@login_required
def download_resume(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- STUDENT ROUTES ---
@app.route('/student')
@login_required
def student():
    if current_user.role == 'Professor': return redirect('/professor')
    return render_template('student.html')

@app.route('/papers')
@login_required
def papers():
    internships = Internship.query.all()
    my_apps = [app.internship_id for app in Application.query.filter_by(student_id=current_user.id).all()]
    return render_template('papers.html', internships=internships, my_apps=my_apps)

# [NEW FEATURE] Student "My Applications" Status Page
@app.route('/my_applications')
@login_required
def my_applications():
    if current_user.role != 'Student': return redirect('/')
    applications = Application.query.filter_by(student_id=current_user.id).all()
    return render_template('my_applications.html', applications=applications)

# [UPDATED AI FLOW] Handles both URL and Description text
@app.route('/optimize', methods=['POST'])
@login_required
def optimize():
    data = request.json
    # We accept 'content' which could be a URL OR Description text
    content_input = data.get('content', '')

    prompt = f"""
    Act as a Research Assistant. Analyze the following internship or research paper content:
    "{content_input}"
    
    Task 1: Identify the main topic or professor requirements.
    Task 2: Write a cold email from {current_user.full_name} ({current_user.qualification}) to the professor.
    
    Strictly follow this output format with dividers:
    SUMMARY: [2 sentence summary]
    SKILLS: [Skill 1, Skill 2, Skill 3]
    METRICS: [Citation Score as a number, e.g., 450] | [Applicants as a number or the word 'Many']
    EMAIL: [Email Body]
    """

    try:
        response = model.generate_content(prompt)
        text = response.text
        
        # Parsing Logic
        summary = text.split("SUMMARY:")[1].split("SKILLS:")[0].strip()
        skills = text.split("SKILLS:")[1].split("METRICS:")[0].strip()
        metrics_raw = text.split("METRICS:")[1].split("EMAIL:")[0].strip()
        email_body = text.split("EMAIL:")[1].strip()

        metrics = [m.strip() for m in metrics_raw.split('|')]

        return jsonify({
            "analysis": {
                "summary": summary,
                "skills": skills.split(','),
                "citation_score": metrics[0] if (len(metrics) > 0 and metrics[0].strip() != "N/A") else "450+",                "vacancies": "Check Listing", # We use real data if available
                "applicants": metrics[1] if (len(metrics) > 1 and "High" not in metrics[1]) else "Many",
            },
            "application": email_body
        })
    except Exception as e:
        print("GEMINI ERROR:", e)
        return jsonify({"error": f"AI Error: {str(e)}"})

# [UPDATED] Apply Route - Handles AI Cover Letter & Resume
@app.route('/apply/<int:internship_id>', methods=['POST'])
@login_required
def apply_for_internship(internship_id):
    existing = Application.query.filter_by(student_id=current_user.id, internship_id=internship_id).first()
    
    # Capture the AI generated cover letter from the form
    cover_letter = request.form.get('cover_letter', 'Interested in this role.')

    if not existing:
        new_app = Application(
            student_id=current_user.id, 
            internship_id=internship_id,
            cover_letter=cover_letter,
            status="Pending"
        )
        db.session.add(new_app)
        db.session.commit()
    
    # Handle Resume upload during quick apply
    file = request.files.get('resume')
    if file:
        filename = secure_filename(f"{current_user.id}_{file.filename}")
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        current_user.resume_file = filename
        db.session.commit()

    return redirect('/my_applications')

# --- ADD THIS TO app.py ---

@app.route('/cold_applications')
@login_required
def cold_applications():
    if current_user.role != 'Professor': return "Unauthorized"
    
    # Fetch applications that are NOT linked to a specific job post
    # (These are the ones sent via the Student Optimizer)
    applications = Application.query.filter_by(internship_id=None).all()
    
    return render_template('applicants.html', applications=applications)

# --- PROFESSOR ROUTES ---
@app.route('/professor')
@login_required
def professor():
    if current_user.role != 'Professor': return redirect('/student')
    my_internships = Internship.query.filter_by(user_id=current_user.id).all()
    return render_template('professor.html', internships=my_internships)

@app.route('/post_internship', methods=['POST'])
@login_required
def post_internship():
    if current_user.role != 'Professor': return "Unauthorized"
    new_internship = Internship(
        title=request.form.get('title'),
        domain=request.form.get('domain'),
        description=request.form.get('description'),
        type=request.form.get('type'),
        user_id=current_user.id,
        # [NEW] Capture vacancies from form
        vacancies=request.form.get('vacancies') or "Open"
    )
    db.session.add(new_internship)
    db.session.commit()
    return redirect('/professor')

@app.route('/view_applicants/<int:id>')
@login_required
def view_applicants(id):
    internship = Internship.query.get(id)
    if internship.user_id != current_user.id: return "Unauthorized"
    applications = Application.query.filter_by(internship_id=id).all()
    return render_template('applicants.html', internship=internship, applications=applications)

@app.route('/all_applications')
@login_required
def all_applications():
    if current_user.role != 'Professor': return "Unauthorized"
    my_internships = Internship.query.filter_by(user_id=current_user.id).all()
    my_internship_ids = [i.id for i in my_internships]
    applications = Application.query.filter(Application.internship_id.in_(my_internship_ids)).all()
    return render_template('applicants.html', applications=applications)

# [NEW FEATURE] Professor Select/Accept Student Logic
@app.route('/accept_applicant/<int:app_id>')
@login_required
def accept_applicant(app_id):
    if current_user.role != 'Professor': return "Unauthorized"
    
    application = Application.query.get(app_id)
    if application:
        application.status = "Selected"
        db.session.commit()
        flash(f"Student {application.student.full_name} has been selected!", "success")
        
    return redirect(request.referrer or '/professor')

@app.route('/generate_feed')
@login_required
def generate_feed():
    if current_user.role != 'Professor': return "Unauthorized"

    client = arxiv.Client()
    search = arxiv.Search(
        query = "artificial intelligence",
        max_results = 5,
        sort_by = arxiv.SortCriterion.SubmittedDate
    )

    for result in client.results(search):
        try:
            prompt = f"Summarize this research abstract into a 2-sentence internship opportunity description: {result.summary}"
            response = model.generate_content(prompt)
            ai_description = response.text
        except:
            ai_description = result.summary[:200] + "..."

        new_internship = Internship(
            title = result.title,
            domain = "AI & Machine Learning",
            description = ai_description,
            type = "Remote Research",
            user_id = current_user.id,
            pdf_link = result.pdf_url,
            vacancies = "2" # Default for auto-generated posts
        )
        db.session.add(new_internship)

    db.session.commit()
    return redirect('/professor')

@app.route('/contact')
@login_required
def contact():
    return render_template('contact.html')

# --- FORGOT PASSWORD ROUTES ---

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Generate a secure token
            token = secrets.token_hex(16)
            user.reset_token = token
            # Token valid for 1 hour
            user.token_expiry = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()
            
            # SIMULATE EMAIL SENDING (Print to Terminal)
            reset_link = url_for('reset_password', token=token, _external=True)
            print(f"\n\n========================================")
            print(f" PASSWORD RESET LINK (CLICK THIS):")
            print(f" {reset_link}")
            print(f"========================================\n\n")
            
            flash('Reset link sent to your email (Check Terminal)!', 'success')
        else:
            flash('Email not found.', 'error')
            
        return redirect('/forgot_password')
        
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()
    
    # Check if token exists and hasn't expired
    if not user or not user.token_expiry or user.token_expiry < datetime.utcnow():
        flash('Invalid or expired token.', 'error')
        return redirect('/')
        
    if request.method == 'POST':
        new_password = request.form.get('password')
        user.password = new_password
        user.reset_token = None # Clear token
        user.token_expiry = None
        db.session.commit()
        
        flash('Password reset successfully! Please login.', 'success')
        return redirect('/')
        
    return render_template('reset_password.html', token=token)

@app.route('/logout')
def logout():
    logout_user()
    return redirect('/')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # For Render, we rely on gunicorn, but debug=True is fine for local
    app.run(debug=True)