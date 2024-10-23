from chalice import Chalice
from chalice import NotFoundError
import boto3
import os
import time

app = Chalice(app_name='chaws')

DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'Usera')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)

notes_storage = {}

@app.route('/save_note', methods=['POST'])
def save_note():
    request = app.current_request
    note_data = request.json_body
    
    username = note_data['username']
    note_id = str(time.time())
    note = {
        'note_id': note_id,
        'original_note': note_data['original_note'],
        'translated_note': note_data['translated_note'],
        'original_audio_url': note_data['original_audio_url'],
        'translated_audio_url': note_data['translated_audio_url']
    }
    
    if username not in notes_storage:
        notes_storage[username] = []
    
    notes_storage[username].append(note)
     
    return {'message': 'Note saved successfully', 'note_id': note_id}

@app.route('/get_notes/{username}', methods=['GET'])
def get_notes(username):
    if username in notes_storage:
        return notes_storage[username]
    else:
        return []

@app.route('/delete_note/{username}/{note_id}', methods=['DELETE'])
def delete_note(username, note_id):
    if username in notes_storage:
        notes_storage[username] = [note for note in notes_storage[username] if note['note_id'] != note_id]
        return {'message': 'Note deleted successfully'}
    else:
        raise NotFoundError('User not found')

