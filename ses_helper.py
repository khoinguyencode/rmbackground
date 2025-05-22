import re
from flask import request
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

aws_access_key = os.getenv("SES_ACCESS_KEY")
aws_secret_key = os.getenv("SES_SECRET_KEY")
aws_region = os.getenv("SES_REGION")

ses = boto3.client(
    'ses',
    region_name=aws_region,
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key
)

def send_email_with_image_link(to_email, image_url):
    try:
        response = ses.send_email(
            Source='dnm779966@gmail.com',
            Destination={'ToAddresses': [to_email]},
            Message={
                'Subject': {'Data': 'Your processed image is ready'},
                'Body': {
                    'Text': {
                        'Data': f'Your image with removed background is ready to download:\n{image_url}'
                    }
                }
            }
        )
        print("Email sent! Message ID:", response['MessageId'])
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

def verify_email(email):
    try:
        # Check if email param was passed directly
        if not email:
            return False
            
        # Validate email format
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            return False
            
        # Try to verify the email identity
        ses.verify_email_identity(EmailAddress=email)
        return True
    except Exception as e:
        print(f"Failed to send verification email: {e}")
        return False


def get_email_verification_status(email):
    try:
        response = ses.get_identity_verification_attributes(
            Identities=[email]
        )
        attrs = response['VerificationAttributes']
        if email in attrs:
            status = attrs[email]['VerificationStatus']
            print(f"Verification status for {email}: {status}")
            return status
        else:
            print(f"No verification info found for {email}")
            return None
    except Exception as e:
        print(f"Error fetching verification status: {e}")
        return None
