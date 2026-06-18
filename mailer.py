import yagmail
import os
from dotenv import load_dotenv
 
load_dotenv()
 
def send_alert(subject, body):
    """
    Send email alert with proper error handling and security.
    
    Requirements:
    - Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env file
    - Use Gmail App Password (not main password)
    - Enable 2FA on Gmail account
    """
    
    try:
        email = os.getenv("GMAIL_ADDRESS")
        password = os.getenv("GMAIL_APP_PASSWORD")
        
        # Validate credentials are present
        if not email or not password:
            print("❌ Error: Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD in .env")
            return False
        
        # Create SMTP connection
        yag = yagmail.SMTP(email, password)
        
        # Send email
        yag.send(
            to=email,
            subject=subject,
            contents=body
        )
        
        yag.close()
        print(f"✅ Alert sent: {subject}")
        return True
        
    except Exception as e:
        print(f"❌ Email sending failed: {e}")
        return False
 
 
def send_task_alert(task_info):
    """Send formatted task alert"""
    subject = f"⚠️ High Priority Task: {task_info.get('task', 'Unnamed')}"
    
    body = f"""
    <h2>High Priority Task Alert</h2>
    
    <p><strong>Task:</strong> {task_info.get('task', 'N/A')}</p>
    <p><strong>Owner:</strong> {task_info.get('owner', 'N/A')}</p>
    <p><strong>Deadline:</strong> {task_info.get('deadline', 'N/A')}</p>
    <p><strong>Priority:</strong> {task_info.get('priority', 'N/A')}</p>
    
    <p><em>This is an automated alert from the AI Meeting Agent.</em></p>
    """
    
    return send_alert(subject, body)