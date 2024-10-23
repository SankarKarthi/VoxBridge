import os
import time
import requests
import streamlit as st
import boto3
import bcrypt
import speech_recognition as sr
from gtts import gTTS
from googletrans import Translator
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
import cv2
import threading
import tempfile
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip

dynamodb = boto3.resource('dynamodb', region_name='us-west-2')
user_table_name = 'Usera'
feedback_table_name = 'Feedback'
s3_client = boto3.client('s3')
S3_BUCKET_NAME = 'notesaver'
API_URL = 'https://be4laa0mr0.execute-api.us-west-2.amazonaws.com/api/'  

translator = Translator()

def hash_password(password):
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(stored_password, provided_password):
    return bcrypt.checkpw(provided_password.encode('utf-8'), stored_password.encode('utf-8'))

def save_user_to_dynamodb(username, hashed_password):
    try:
        table = dynamodb.Table(user_table_name)
        response = table.put_item(
            Item={
                'username': username,
                'hashed_password': hashed_password
            }
        )
        return response
    except (NoCredentialsError, PartialCredentialsError):
        st.write("AWS credentials not found. Please configure AWS credentials.")
        return None

def get_user_from_dynamodb(username):
    table = dynamodb.Table(user_table_name)
    response = table.get_item(
        Key={'username': username}
    )
    return response.get('Item', None)

def upload_file_to_s3(file_path, bucket, object_name=None):
    if object_name is None:
        object_name = os.path.basename(file_path)
    try:
        s3_client.upload_file(file_path, bucket, object_name)
        return f"https://{bucket}.s3.amazonaws.com/{object_name}"
    except NoCredentialsError:
        st.error("AWS credentials not available")
        return None

def record_video(stop_event, video_path):
    cap = cv2.VideoCapture(0)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(video_path, fourcc, 20.0, (640, 480))
    
    while not stop_event.is_set():
        ret, frame = cap.read()
        if ret:
            out.write(frame)
    
    cap.release()
    out.release()

def combine_audio_video(video_path, audio_path, output_path):
    video = VideoFileClip(video_path)
    audio = AudioFileClip(audio_path)
    
    video = video.set_audio(audio)
    video.write_videofile(output_path, codec='libx264', audio_codec='aac')
    
def get_s3_presigned_url(bucket_name, object_name, expiration=3600):
    """Generate a presigned URL to share an S3 object"""
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': bucket_name,
                                                            'Key': object_name},
                                                    ExpiresIn=expiration)
    except NoCredentialsError:
        st.error("AWS credentials not available")
        return None
    return response

def save_audio_to_s3(text, filename, language, is_translated=False):
    if is_translated:
        translation = translator.translate(text, src=language, dest='en')
        text = translation.text 
        filename = f"translated_{filename}"  
    audio = gTTS(text=text, lang='en', slow=False)
    audio.save(filename)
    
    s3_url = upload_file_to_s3(filename, S3_BUCKET_NAME)
    os.remove(filename) 
    return s3_url

def save_note_to_chalice(username, original_note, translated_note, original_audio_url, translated_audio_url, combined_url):
    url = f"{API_URL}/save_note"
    payload = {
        'username': username,
        'original_note': original_note,
        'translated_note': translated_note,
        'original_audio_url': original_audio_url,
        'translated_audio_url': translated_audio_url,
        'combined_url': combined_url
    }
    response = requests.post(url, json=payload)
    return response.status_code == 200


def read_notes_from_chalice(username):
    url = f"{API_URL}/get_notes/{username}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return []

def delete_note_from_chalice(username, note_id):
    url = f"{API_URL}/delete_note/{username}/{note_id}"
    response = requests.delete(url)
    return response.status_code == 200

def feedback_page():
    st.title("User Feedback")
    
    if not st.session_state.get('logged_in', False):
        st.warning("Please log in to submit feedback.")
        return

    feedback_text = st.text_area("Enter your feedback here:", height=150)
    submit_feedback = st.button("Submit Feedback")

    if submit_feedback:
        if feedback_text.strip() == "":
            st.error("Feedback cannot be empty!")
        else:
            if save_feedback_to_dynamodb(st.session_state.current_username, feedback_text):
                st.success("Feedback submitted successfully!")
            else:
                st.error("There was an issue submitting your feedback.")

def save_feedback_to_dynamodb(username, feedback):
    try:
        table = dynamodb.Table(feedback_table_name)
        response = table.put_item(
            Item={
                'username': username,
                'feedback': feedback
            }
        )
        return response
    except (NoCredentialsError, PartialCredentialsError):
        st.write("AWS credentials not found. Please configure AWS credentials.")
        return None
    
def take_note_with_video(language):
    r = sr.Recognizer()
    
    # Create temporary files for audio, video, and combined output
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file, \
         tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as video_file, \
         tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as combined_file:
        
        audio_path = audio_file.name
        video_path = video_file.name
        combined_path = combined_file.name

    # Start video recording in a separate thread
    stop_event = threading.Event()
    video_thread = threading.Thread(target=record_video, args=(stop_event, video_path))
    video_thread.start()

    with sr.Microphone() as source:
        st.write("Say something...")
        r.adjust_for_ambient_noise(source)
        audio = r.listen(source)

        # Stop video recording
        stop_event.set()
        video_thread.join()

        try:
            note = r.recognize_google(audio, language=language)
            st.write("You said:", note)

            translation = translator.translate(note, src=language, dest='en')
            translated_note = translation.text
            
            # Save audio to file
            with open(audio_path, "wb") as f:
                f.write(audio.get_wav_data())

            # Combine audio and video
            combine_audio_video(video_path, audio_path, combined_path)

            # Upload audio and combined file to S3
            timestamp = int(time.time())
            audio_s3_path = f"audio_{timestamp}.wav"
            combined_s3_path = f"combined_{timestamp}.mp4"
            
            audio_url = upload_file_to_s3(audio_path, S3_BUCKET_NAME, audio_s3_path)
            combined_url = upload_file_to_s3(combined_path, S3_BUCKET_NAME, combined_s3_path)

            # Generate and upload translated audio
            translated_audio_path = f"translated_audio_{timestamp}.mp3"
            tts = gTTS(text=translated_note, lang='en', slow=False)
            tts.save(translated_audio_path)
            translated_audio_url = upload_file_to_s3(translated_audio_path, S3_BUCKET_NAME)

            # Clean up temporary files
            os.unlink(audio_path)
            os.unlink(video_path)
            os.unlink(combined_path)
            os.unlink(translated_audio_path)

            return note, translated_note, audio_url, translated_audio_url, combined_url
        except sr.UnknownValueError:
            st.write("Sorry, I could not understand what you said.")
            return "", "", None, None, None
        except sr.RequestError:
            st.write("Sorry, I'm having trouble accessing the Google API. Please try again later.")
            return "", "", None, None, None



def home_page():
    st.title("VOICE TAKER")
    
    st.subheader("User Authentication")

    st.write("Home page options:")
    tabs = st.tabs(["Log In", "Sign Up"])

    with tabs[1]:
        st.subheader("Sign Up")
        new_username = st.text_input("New Username", key='new_username')
        new_password = st.text_input("New Password", type="password", key='new_password')
        confirm_password = st.text_input("Confirm Password", type="password", key='confirm_password')
        sign_up = st.button("Sign Up")

        if sign_up:
            if new_password != confirm_password:
                st.error("Please enter your password correctly!")
            else:
                hashed_password = hash_password(new_password)
                user_saved = save_user_to_dynamodb(new_username, hashed_password)
                
                if user_saved:
                    st.success("Sign up successful! Please log in.")
                else:
                    st.error("There was an issue saving your credentials.")

    with tabs[0]:
        st.subheader("Log In")
        username = st.text_input("Username", key='login_username')
        password = st.text_input("Password", type="password", key='login_password')
        login = st.button("Login")

        if login:
            user = get_user_from_dynamodb(username)
            
            if user is None:
                st.error("User not found!")
            else:
                stored_password = user['hashed_password']
                
                if verify_password(stored_password, password):
                    st.session_state.logged_in = True
                    st.session_state.current_username = username
                    st.success("Login successful!")
                    
                else:
                    st.error("Incorrect password. Please try again.")

def notes_page(language):
    if not st.session_state.get('logged_in', False):
        st.warning("Please log in to access the notes page.")
        return

    st.title("NOTES")
    
    st.write("Notes page options:")
    tabs = st.tabs(["Take Note", "Read Notes"])

    with tabs[0]:
        st.subheader("Take Note")

        if st.button("Start Recording"):
            with st.spinner("Recording... Press Ctrl+C to stop"):
                original_note_text, translated_note_text, original_audio_url, translated_audio_url, combined_url = take_note_with_video(language=language)
                
                if original_note_text:
                    if save_note_to_chalice(st.session_state.current_username, original_note_text, translated_note_text, original_audio_url, translated_audio_url, combined_url):
                        st.success("Note saved successfully!")

    with tabs[1]:
        st.subheader("Read Notes")
        username = st.session_state.current_username
        notes = read_notes_from_chalice(username)
        
        if notes:
            for note in notes:
                note_id = note['note_id']
                original_note = note['original_note']
                translated_note = note['translated_note']
                original_audio_url = note.get('original_audio_url')
                translated_audio_url = note.get('translated_audio_url')
                
                st.write("Original Note:", original_note)
                st.write("Translated Note:", translated_note)

                if st.button(f"Play Original Audio {note_id}"):
                    if original_audio_url:
                        original_presigned_url = get_s3_presigned_url(S3_BUCKET_NAME, original_audio_url.split('/')[-1])
                        if original_presigned_url:
                            st.audio(original_presigned_url, format='audio/mp3')
                        else:
                            st.error("Unable to generate presigned URL for original audio")
                    else:
                        st.error("Original audio not available.")

                if st.button(f"Play Translated Audio {note_id}"):
                    if translated_audio_url:
                        translated_presigned_url = get_s3_presigned_url(S3_BUCKET_NAME, translated_audio_url.split('/')[-1])
                        if translated_presigned_url:
                            st.audio(translated_presigned_url, format='audio/mp3')
                        else:
                            st.error("Unable to generate presigned URL for translated audio")
                    else:
                        st.error("Translated audio not available.")

                if st.button(f"Delete Note {note_id}"):
                    if delete_note_from_chalice(username, note_id):
                        st.success("Note deleted successfully!")
                        st.experimental_rerun()

def main():
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False

    st.sidebar.title("Navigation")
    page = st.sidebar.selectbox("Go to", ["Home", "Notes", "Feedback"])

    if page == "Home":
        home_page()

    elif page == "Notes":
        if st.session_state.logged_in:
            language = st.selectbox("Select Language", ["ta", "es", "fr", "en", "hi", "ml", "te"])
            notes_page(language)
        else:
            st.warning("Please log in to access the notes page.")

    elif page == "Feedback":
        feedback_page()


if __name__ == "__main__":
    main()