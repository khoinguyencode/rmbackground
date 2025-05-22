import mysql.connector
from dotenv import load_dotenv
import os

from ses_helper import get_email_verification_status

load_dotenv()

def get_db_connection():
    try:
        # Check if environment variables are set
        required_vars = ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'DB_PORT']
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        
        if missing_vars:
            error_msg = f"Missing database environment variables: {', '.join(missing_vars)}"
            print(error_msg)
            return None
            
        # Try to establish connection
        connection = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME'),
            port=int(os.getenv('DB_PORT')),
            connection_timeout=5
        )
        return connection
    except mysql.connector.Error as e:
        print(f"Database connection error: {e}")
        return None

def get_user_by_username_and_password(username, password):
    connection = get_db_connection()
    if not connection:
        print("Database connection failed")
        return None
        
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute('SELECT * FROM users WHERE username = %s AND password = %s', (username, password))
        user = cursor.fetchone()
        cursor.close()
        return user
    except Exception as e:
        print(f"Error getting user: {e}")
        return None
    finally:
        connection.close()

def get_user_attempts(username):
    connection = get_db_connection()
    if not connection:
        print("Database connection failed")
        return None
        
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute('SELECT login_attempts FROM users WHERE username = %s', (username,))
        result = cursor.fetchone()
        cursor.close()
        return result
    except Exception as e:
        print(f"Error getting user attempts: {e}")
        return None
    finally:
        connection.close()

def update_user_attempts(username, attempts):
    connection = get_db_connection()
    if not connection:
        print("Database connection failed")
        return False
        
    try:
        cursor = connection.cursor()
        cursor.execute('UPDATE users SET login_attempts = %s WHERE username = %s', (attempts, username))
        connection.commit()
        cursor.close()
        return True
    except Exception as e:
        print(f"Error updating user attempts: {e}")
        return False
    finally:
        connection.close()

def create_user(username, password_hash, email):
    connection = get_db_connection()
    if not connection:
        print("Database connection failed")
        raise Exception("Failed to connect to database")
        
    try:
        cursor = connection.cursor()
        cursor.execute('INSERT INTO users (username, password, email) VALUES (%s, %s, %s)', 
                     (username, password_hash, email))
        connection.commit()
        cursor.close()
        return True
    except Exception as e:
        print(f"Error creating user: {e}")
        raise
    finally:
        connection.close()

def update_verification_status(email):
    status = get_email_verification_status(email)
    print(f"Email verification status for {email}: {status}")
    
    if status == "Success":
        connection = get_db_connection()
        if not connection:
            print("Database connection failed")
            return False
            
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
            user = cursor.fetchone()
            
            if user:
                cursor.execute('UPDATE users SET verified = %s WHERE email = %s', (True, email))
                connection.commit()
                print(f"{email} has been verified and updated in database.")
                result = True
            else:
                print(f"User with email {email} does not exist in database.")
                result = False
                
            cursor.close()
            return result
        except Exception as e:
            print(f"Error updating verification status: {e}")
            return False
        finally:
            connection.close()
    else:
        print(f"{email} is not verified.")
        return False


def is_user_verified(email):
    connection = get_db_connection()
    if not connection:
        print("Database connection failed")
        return False
        
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute('SELECT verified FROM users WHERE email = %s', (email,))
        result = cursor.fetchone()
        cursor.close()
        return result['verified'] if result else False
    except Exception as e:
        print(f"Error checking user verification: {e}")
        return False
    finally:
        connection.close()
