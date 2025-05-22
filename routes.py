import re
from flask import Blueprint, render_template, request, flash, redirect, send_file, url_for, session
from rembg import remove
from PIL import Image, UnidentifiedImageError
from io import BytesIO
import logging
import os
import time
import hashlib
from db import create_user, get_db_connection, get_user_by_username_and_password, get_user_attempts, update_user_attempts, update_verification_status
from s3_helper import upload_to_s3, get_s3_url, download_from_s3
from dotenv import load_dotenv
from ses_helper import send_email_with_image_link, verify_email
from urllib.parse import urlparse

load_dotenv()

bp = Blueprint('main', __name__)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
BUCKET_NAME = os.getenv("BUCKET_NAME")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form['username']
            password = request.form['password']
            password_hash = hashlib.sha256(password.encode()).hexdigest()

            user = get_user_by_username_and_password(username, password_hash)
            
            if user is None:
                # Could be database error
                flash('Login service temporarily unavailable. Please try again later.', 'danger')
                return render_template('login.html')

            if user:
                # Check if user has verified their email
                if not user.get('verified'):
                    # Try to verify again from SES
                    is_verified = update_verification_status(user['email'])
                    if not is_verified:
                        flash('Your email is not verified. Please check your inbox and verify your email before logging in.', 'warning')
                        return redirect(url_for('main.login')) 

                # Login successful
                session['user_id'] = user['id']
                session['user_email'] = user['email']
                flash('Login successful', 'success')
                return redirect(url_for('main.upload_file'))

            # Wrong password or user not found
            user_attempts = get_user_attempts(username)
            if user_attempts is None:
                # Database error
                flash('Login service temporarily unavailable. Please try again later.', 'danger')
                return render_template('login.html')
                
            if user_attempts and user_attempts['login_attempts'] >= 3:
                flash('Your account is locked due to too many failed login attempts.', 'danger')
            else:
                if user_attempts:
                    new_attempts = user_attempts['login_attempts'] + 1
                    update_result = update_user_attempts(username, new_attempts)
                    if not update_result:
                        # Failed to update attempts
                        logging.error(f"Failed to update login attempts for user {username}")
                flash('Invalid credentials. Please try again.', 'danger')
                
        except Exception as e:
            logging.error(f"Login error: {e}", exc_info=True)
            flash('Login service temporarily unavailable. Please try again later.', 'danger')

    return render_template('login.html')

@bp.route('/', methods=['GET', 'POST'])
def upload_file():
    logged_in = 'user_id' in session

    if 'upload_count' not in session:
        session['upload_count'] = 0

    if request.method == 'POST':
        if not logged_in and session['upload_count'] >= 3:
            flash('You have reached the upload limit. Please log in to continue.', 'danger')
            return render_template('index.html', logged_in=False)

        if 'file' not in request.files:
            flash('No file part in the request', 'danger')
            return render_template('index.html', logged_in=logged_in)

        file = request.files['file']

        if file.filename == '':
            flash('No file selected', 'danger')
            return render_template('index.html', logged_in=logged_in)

        if not allowed_file(file.filename):
            flash('Only PNG, JPG, JPEG files allowed.', 'danger')
            return render_template('index.html', logged_in=logged_in)

        try:
            # Save the uploaded file to memory first to ensure it's properly read
            file_bytes = file.read()
            if not file_bytes:
                flash('Empty file uploaded', 'danger')
                return render_template('index.html', logged_in=logged_in)
                
            # Reset file pointer
            file_stream = BytesIO(file_bytes)
            
            try:
                input_image = Image.open(file_stream).convert("RGBA")
            except UnidentifiedImageError:
                flash('Invalid image file.', 'danger')
                return render_template('index.html', logged_in=logged_in)

            # Prepare input for rembg
            input_bytes = BytesIO()
            input_image.save(input_bytes, format='PNG')
            input_bytes = input_bytes.getvalue()
            
            if not input_bytes:
                flash('Failed to process image data', 'danger')
                return render_template('index.html', logged_in=logged_in)

            # Process with rembg
            output_bytes = remove(input_bytes)
            
            if not output_bytes:
                flash('Background removal failed - empty result', 'danger')
                return render_template('index.html', logged_in=logged_in)
                
            output_image = Image.open(BytesIO(output_bytes))

            timestamp = int(time.time())
            filename = f"image_rmbg_{timestamp}.png"
            
            # Create directory if it doesn't exist
            os.makedirs(os.path.join('static', 'processed'), exist_ok=True)
            
            # Save locally for processing
            save_path = os.path.join('static', 'processed', filename)
            output_image.save(save_path)
            
            # Upload to S3 and get CloudFront URL
            s3_key = f"remove-background-imgs/{filename}"
            with open(save_path, 'rb') as image_file:
                upload_success = upload_to_s3(image_file, BUCKET_NAME, s3_key)
                if not upload_success:
                    flash('Failed to upload processed image to storage', 'danger')
                    return render_template('index.html', logged_in=logged_in)
                
            # Get CloudFront URL
            cloudfront_url = get_s3_url(BUCKET_NAME, s3_key)
            
            # Store in database if user is logged in
            if logged_in:
                try:
                    user_id = session['user_id']
                    connection = get_db_connection()
                    if connection:
                        cursor = connection.cursor()
                        # Store the processed image URL in the images table
                        cursor.execute(
                            'INSERT INTO images (user_id, url_background) VALUES (%s, %s)',
                            (user_id, cloudfront_url)
                        )
                        connection.commit()
                        cursor.close()
                        connection.close()
                        print(f"Stored processed image URL in database for user {user_id}")
                except Exception as e:
                    logging.error(f"Failed to store image in database: {e}")
                    # Continue even if DB storage fails - we still have the image

            if not logged_in:
                session['upload_count'] += 1

            # Use CloudFront URL for the result page
            return render_template("result.html", img_url=cloudfront_url, filename=filename, logged_in=logged_in)

        except Exception as e:
            logging.error("Exception occurred", exc_info=True)
            flash(f'Processing failed: {e}', 'danger')
            return render_template('index.html', logged_in=logged_in)

    return render_template('index.html', logged_in=logged_in)

@bp.route('/download/<filename>')
def download_file(filename):
    if not allowed_file(filename):
        flash('Invalid file format for download.', 'danger')
        return redirect(url_for('main.upload_file'))

    file_path = os.path.join('static', 'processed', filename)
    if not os.path.exists(file_path):
        flash("File not found.", 'danger')
        return redirect(url_for('main.upload_file'))

    return send_file(file_path, mimetype='image/png', as_attachment=True, download_name=filename)


# Route đăng xuất
@bp.route('/logout')
def logout():
    session.pop('user_id', None)
    session['upload_count'] = 0 
    flash('You have been logged out.', 'success')
    return redirect(url_for('main.upload_file'))

# Route đăng ký
@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form['email']
        
        # Kiểm tra dinh dạng email
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash('Invalid email format.', 'danger')
            return render_template('signup.html')
        
        # Mã hóa mật khẩu
        password_hash = hashlib.sha256(password.encode()).hexdigest()

        # Kiểm tra xem người dùng đã tồn tại chưa
        existing_user = get_user_by_username_and_password(username, password_hash)
        if existing_user:
            flash('Username already exists. Please choose another one.', 'danger')
            return render_template('signup.html')

        # Gọi hàm verify_email ở ses_helper.py
        if not verify_email(email):
            flash('Email verification failed. Please try again.', 'danger')
            return render_template('signup.html')
        
        # Thông báo đã gửi email xác thực
        flash('Verification email sent. Please check your inbox.', 'success')
        
        # Tạo người dùng mới
        try:
            create_user(username, password_hash, email)
            flash('Registration successful. Please verify your email before logging in.', 'success')
            return redirect(url_for('main.login'))
        except Exception as e:
            logging.error(f"Failed to create user: {e}", exc_info=True)
            flash('An error occurred during registration. Please try again.', 'danger')
            return render_template('signup.html')

    return render_template('signup.html')

@bp.route('/change-background/<filename>', methods=['GET'])
def change_background(filename):
    if 'user_id' not in session:
        flash('You need to login to select background.', 'warning')
        return redirect(url_for('main.login'))

    user_id = session['user_id']

    # Kết nối db lấy url_background theo user_id
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute('SELECT url_background FROM images WHERE user_id = %s', (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    connection.close()

    # Lấy danh sách URL background
    background_urls = [row['url_background'] for row in rows]

    return render_template("change_background.html", filename=filename, backgrounds=background_urls, logged_in=user_id)



@bp.route('/apply-background', methods=['POST'])
def apply_background():
    from PIL import ImageOps
    from urllib.parse import urlparse
    print("Session inside apply_background:", dict(session))

    filename = request.form.get('filename')
    background_url = request.form.get('background_url')

    if not filename or not background_url:
        flash("Missing required data to apply background.", 'danger')
        return redirect(url_for('main.upload_file'))

    try:
        fg_path = os.path.join('static', 'processed', filename)
        foreground = Image.open(fg_path).convert("RGBA")

        parsed = urlparse(background_url)
        bg_key = parsed.path.lstrip('/')

        bg_file = download_from_s3(BUCKET_NAME, bg_key)
        background = Image.open(bg_file).convert("RGBA").resize(foreground.size)

        result = Image.alpha_composite(background, foreground)

        timestamp = int(time.time())
        new_filename = f"final_{timestamp}.png"
        
        # Save locally for processing
        save_path = os.path.join('static', 'processed', new_filename)
        result.save(save_path)
        
        # Upload to S3 and get CloudFront URL
        s3_key = f"remove-background-imgs/{new_filename}"
        with open(save_path, 'rb') as image_file:
            upload_to_s3(image_file, BUCKET_NAME, s3_key)
            
        # Get CloudFront URL
        cloudfront_url = get_s3_url(BUCKET_NAME, s3_key)
        
        user_id = session['user_id']

        return render_template("result.html", img_url=cloudfront_url, filename=new_filename, logged_in='user_id' in session)

    except Exception as e:
        logging.error("Background change failed", exc_info=True)
        flash(f"Failed to apply background: {e}", 'danger')
        return redirect(url_for('main.upload_file'))

@bp.route('/upload-background', methods=['POST'])
def upload_background():
    from werkzeug.utils import secure_filename

    if 'user_id' not in session:
        flash('You must be logged in to upload a background.', 'danger')
        return redirect(url_for('main.login'))

    user_id = session['user_id']
    print("User ID at upload start:", user_id) 

    background_file = request.files.get('background_file')
    filename_param = request.form.get('filename')

    if not background_file or background_file.filename == '':
        flash('No background file selected.', 'danger')
        return redirect(url_for('main.change_background', filename=filename_param))

    if not allowed_file(background_file.filename):
        flash('Only PNG, JPG, JPEG files are allowed.', 'danger')
        return redirect(url_for('main.change_background', filename=filename_param))

    try:
        filename = secure_filename(background_file.filename)
        timestamped_name = f"remove-background-imgs/{int(time.time())}_{filename}"

        upload_to_s3(background_file, BUCKET_NAME, timestamped_name)
        bg_url = get_s3_url(BUCKET_NAME, timestamped_name)

        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            'INSERT INTO images (user_id, url_background) VALUES (%s, %s)',
            (user_id, bg_url)
        )
        connection.commit()
        cursor.close()
        connection.close()

        flash('Background uploaded successfully. You can now select it.', 'success')
        print("User ID before redirect:", session.get('user_id')) 
        return redirect(url_for('main.change_background', filename=filename_param))

    except Exception as e:
        logging.error("Failed to upload background", exc_info=True)
        flash(f"Failed to upload background: {e}", 'danger')
        return redirect(url_for('main.change_background', filename=filename_param))
