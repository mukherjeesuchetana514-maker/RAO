# Research Application Optimizer (RAO) - Project Documentation
## Page 1: Project Overview & Getting Started
### 1. Executive Summary
Project Name: Research Application Optimizer (RAO)  
Version: 2.0 (AI Powered)  
The Research Application Optimizer (RAO) is a specialized web platform designed to bridge the gap between students seeking research internships and professors looking for talent. Unlike standard job boards, RAO leverages Generative AI (Google Gemini) to analyze research papers and lab websites, helping students generate hyper-personalized cold emails and cover letters. It provides a dual-interface system: a management dashboard for Professors to post opportunities and review applicants, and an "Optimizer" toolkit for Students to find, analyze, and apply to research positions effectively.
### 2. Technical Stack
The application is built as a monolithic web application using the following technologies:
*	Backend Framework: Python Flask (Microframework).
*	Database: SQLite (via Flask-SQLAlchemy ORM).
*	Authentication: Flask-Login (Session management).
*	AI Engine: Google Gemini API (gemini-1.5-flash / gemini-pro).
*	External APIs: * ArXiv API: For fetching trending research papers automatically.
*	DuckDuckGo Search: For retrieving professor lab contexts.
*	Semantic Scholar: For metadata extraction.
*	Frontend: HTML5, CSS3 (Custom styling with extensive use of Flexbox/Grid), JavaScript (Fetch API for async AI operations).
### 3. Installation & Configuration
#### Prerequisites
+	Python 3.8 or higher.
+	A Google Gemini API Key.
+	(Optional) Virtual Environment setup.
#### Setup Instructions
1.	Clone the Repository
    Extract the provided project files into a directory (e.g., RAO_FINAL).
2.	Install Dependencies
    Navigate to the project root and install the required Python packages.
> pip install -r requirements.txt
3.	Environment Configuration
    Create a .env file in the root directory (if not present) and add your API key:
> GEMINI_API_KEY=your_google_gemini_api_key_here
> DATABASE_URL=sqlite:///devsync.db
4.	Database Initialization
    Initialize the database and seed it with default data (like a demo Professor account and trending papers).
> python seed.py
    This script creates instance/devsync.db and adds a default professor (prof@mit.edu / 123).
5.	Running the Application
    Start the Flask development server:
> python app.py
---
The application will launch at http://127.0.0.1:5000/.
## Page 2: System Architecture & Data Models
### 1. Project Structure
The codebase follows a standard Flask structure:
*	app.py: The core controller. Handles all routing, API integration, and business logic.
*	models.py: Defines the database schema.
*	seed.py: Utility script for populating the database with initial data.
*	templates/: Contains HTML files (Jinja2 templates) for the UI.
*	Key Templates: layout.html (Base), student.html (Optimizer), professor.html (Dashboard).
*	static/: Stores uploads (Resumes) and CSS/JS assets.
### 2. Database Schema
The application uses a relational model with three primary entities:
#### User Table
Stores authentication details and profile data for both roles.
*	Fields: id, email, password, role ('Student' or 'Professor'), full_name.
*	Student Specifics: qualification, college, resume_file.
#### Internship Table
Represents a research opportunity or a specific paper open for collaboration.
*	Fields: id, title, domain (e.g., NLP), description (Abstract/Summary), type (Remote/Onsite), pdf_link, vacancies.
*	Foreign Key: user_id (Links to the Professor who posted it).
#### Application Table
Tracks the submission of a student to a specific internship.
*	Fields: id, status (Pending/Selected), cover_letter (AI generated text).
*	Foreign Keys: student_id, internship_id.
### 3. Core Logic & AI Pipeline
The optimize() function in app.py is the application's brain. It executes the following pipeline:
1.	Input: Receives a paper URL or text description from the student.
2.	Metadata Extraction: * Uses ArXiv API or Semantic Scholar to fetch the formal Title, Abstract, and Authors.
*	Attempts to identify the Professor/PI (Principal Investigator).
3.	Context Scraping: * Uses DuckDuckGo to search for the identified Professor's lab website.
*	Scrapes text from the lab website to understand their current research focus.
4.	Generative AI (Gemini):
*	Constructs a complex prompt containing the Paper Abstract, Lab Context, and Student Profile.
*	Output: A JSON object containing:
-	Summary: Simplified explanation of the paper.
-	Skills: Key tech stack required (e.g., PyTorch, Transformers).
-	Analysis: Citation score, vacancy estimation.
-	Draft: A highly specific, personalized cold email.
---
## Page 3: User Guide & Workflows
### 1. Student Workflow
Students use the platform to find opportunities and craft high-quality applications.
*	Dashboard (/student): The "Optimizer" interface.
 *	Input: Paste a link to a research paper (ArXiv) or a job description.
 *	![Image](https://github.com/user-attachments/assets/0bacc27e-c6a1-41bc-a7d0-18c9ea2fce82)
 *	Process: Click "Optimize". The system analyzes the text in real-time.
 *	Result: View a summary, required skills, and an editable AI-drafted email.
*	Trending Opportunities (/papers): A feed of open positions posted by professors.
 *	Students can "Quick Apply" or use "Review & Apply" to let AI analyze the posting before applying.
 *	<img width="1208" height="839" alt="Image" src="https://github.com/user-attachments/assets/f7993565-97b0-402b-aa35-3e5ff8ddb1bc" />
*	Application Tracking (/my_applications): View status of sent applications (Pending/Selected).
*	<img width="1210" height="779" alt="Image" src="https://github.com/user-attachments/assets/19161020-4545-4e5e-8308-4dd2bde73ba4" />
### 2. Professor Workflow
Professors use the platform to manage recruitment overhead.
#### *	Dashboard (/professor):
 *	Post Opportunity: Manually create a listing for a specific project.
 *	Auto-Generate Feed: One-click feature that fetches the latest AI papers from ArXiv and converts them into draft internship listings automatically.
 *	Applicant Review (/cold_applications & /view_applicants):
 *	View a table of candidates.
 *	Actions: Download attached Resumes (PDF), read the specific Cover Letter, and "Select" candidates.
 *	Inbox: A specific inbox for "Cold Applications" (unsolicited applications generated via the Optimizer).
 *	<img width="1208" height="838" alt="Image" src="https://github.com/user-attachments/assets/d904d3da-2233-4cda-b7b3-61af6fd36a39" />
### 3. Key Features Breakdown
| Feature | Description | Tech Used |
| :---       | :---:        | ---:        |
| Smart Drafting     | Creates context-aware emails that reference specific paper details and lab work.      | Gemini AI, Prompt Engineering     |
|Lab Contextualization | Searches the web to find what the professor is currently working on to tailor the email.       | DuckDuckGo, BeautifulSoup      |
|Resume Handling | Secure upload and retrieval of PDF resumes.       |Flask-Uploads, Secure Filename     |
|Auto-Feed | Automatically populates the professor's board with trending research topics.       | ArXiv API    |
### 4. Troubleshooting
*	AI Error: If the "Optimize" button fails, ensure the GEMINI_API_KEY in .env is valid.
*	Database Locks: If using SQLite, avoid opening the DB file in other programs while the app is running.
*	Upload Issues: Ensure the static/uploads folder exists (the app creates it automatically on start).
