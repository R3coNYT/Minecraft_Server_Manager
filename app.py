from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import re
import os
import psutil
import subprocess

app = Flask(__name__)
socketio = SocketIO(app)

LOG_FILE = 'server/server.log'
INPUT_FILE = 'server/minecraft_input.txt'
chat_pattern = re.compile(r'\[\d{2}:\d{2}:\d{2} INFO\]: <([^>]+)> (.+)')
server_directory = os.path.join(os.getcwd(), 'server')
msg_file = 'msg_server.txt'

if not os.path.exists(LOG_FILE):
    open(LOG_FILE, 'a').close()

if not os.path.exists(INPUT_FILE):
    open(INPUT_FILE, 'a').close()

if not os.path.exists(msg_file):
    open(msg_file, 'a').close()

# Vérifie si 'msg_server.txt' contient 'on'. Si ce n'est pas le cas, lance le processus et écris 'on' dans le fichier.
def check_and_start_msg_process():
    try:
        with open(msg_file, 'r') as f:
            status = f.read().strip()
    except FileNotFoundError:
        status = ''

    if status == 'on':
        os.system('rm -r server/chat_messages.log')
        os.system('touch server/chat_messages.log')
        # Lancer le processus send_server_msg.sh
        subprocess.Popen(['./send_server_msg.sh'], cwd=server_directory)
        subprocess.Popen(['./extract_chat_messages.sh'], cwd=server_directory)
        
        # Écrire 'on' dans le fichier pour marquer le processus comme en cours d'exécution
        with open(msg_file, 'w') as f:
            f.write('on')

# Appelez cette fonction lorsque vous voulez vérifier et éventuellement démarrer le processus.
check_and_start_msg_process()

def is_server_running():
    for process in psutil.process_iter(['name', 'cmdline']):
        if 'java' in process.info['name'] and 'mohist.jar' in process.info['cmdline']:
            return True
    return False

def start_server():
    with open('msg_server.txt', 'w') as f:
        f.write('on')
    subprocess.Popen(['./start.sh'], cwd=server_directory)
    subprocess.Popen(['./send_server_msg.sh'], cwd=server_directory)
    subprocess.Popen(['./extract_chat_messages.sh'], cwd=server_directory)

def stop_server():
    os.system('pkill -f send_server_msg.sh')
    os.system('pkill -f extract_chat_messages.sh')
    with open('msg_server.txt', 'w') as f:
        f.write('off')
    os.system('pkill -f java')
    os.system('rm -r server/chat_messages.log')
    os.system('touch server/chat_messages.log')

@app.route('/')
def index():
    server_status = "Démarré" if is_server_running() else "Arrêté"
    return render_template('index.html', server_status=server_status)

@app.route('/logs')
def get_logs():
    """Renvoyer le contenu du fichier de log pour que le client puisse le lire."""
    try:
        with open(LOG_FILE, 'r') as f:
            logs = f.readlines()
        return jsonify({'logs': logs})
    except FileNotFoundError:
        return jsonify({'logs': []})

@app.route('/start_server', methods=['POST'])
def start_server_route():
    if not is_server_running():
        start_server()
    return jsonify({'status': 'Démarré'})

@app.route('/stop_server', methods=['POST'])
def stop_server_route():
    if is_server_running():
        stop_server()
    return jsonify({'status': 'Arrêté'})

@app.route('/chat_messages')
def chat_messages():
    try:
        with open('server/chat_messages.log', 'r') as f:
            messages = f.read().splitlines()
        return jsonify({'messages': messages})
    except FileNotFoundError:
        return jsonify({'messages': []})

@app.route('/send_message', methods=['POST'])
def send_message():
    """Envoyer un message ou une commande au serveur Minecraft via le fichier d'entrée."""
    data = request.get_json()
    message = data.get('message')
    if message:
        # Vérifier si le message est une commande (commence par /)
        if message.startswith('/'):
            # Écrire la commande directement
            with open(INPUT_FILE, 'a') as f:
                f.write(f'{message[1:]}\n')  # Retirer le premier caractère "/" avant d'écrire
        else:
            # Écrire le message avec "say" pour qu'il soit interprété comme un message de chat
            with open(INPUT_FILE, 'a') as f:
                f.write(f'say {message}\n')
        return jsonify({'status': 'Message ou commande envoyé'})
    return jsonify({'status': 'Aucun message reçu'}), 400


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)

