import socket
import sqlite3
import json
import time
import os
from urllib.parse import parse_qs

# --- ROBUST DATABASE SETUP WITH MIGRATION ---
def init_db():
    conn = sqlite3.connect('chat.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipient TEXT,
            text TEXT,
            media_url TEXT,
            media_type TEXT,
            edited INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT,
            last_seen REAL
        )
    ''')
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN lower_username TEXT')
    except sqlite3.OperationalError:
        pass 
        
    conn.commit()
    conn.close()

def run_server():
    init_db()
    HOST = '0.0.0.0'
    PORT = int(os.environ.get("PORT", 8080))

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)
    
    print(f"[*] Netknock Pro Server running on port {PORT}")

    while True:
        client_socket, client_address = server.accept()
        try:
            client_socket.settimeout(3.0)
            
            request_data = b""
            while True:
                try:
                    chunk = client_socket.recv(16384)
                    if not chunk:
                        break
                    request_data += chunk
                    if b'\r\n\r\n' in request_data:
                        if b'Content-Length:' in request_data:
                            header_part = request_data.split(b'\r\n\r\n')[0]
                            cl_line = [l for l in header_part.decode('utf-8', errors='ignore').split('\r\n') if l.lower().startswith('content-length:')]
                            if cl_line:
                                cl_val = int(cl_line[0].split(':')[1].strip())
                                if len(request_data) >= len(header_part) + 4 + cl_val:
                                    break
                        else:
                            break
                except socket.timeout:
                    break

            if not request_data or b'\r\n\r\n' not in request_data:
                client_socket.close()
                continue

            header_bytes, body_bytes = request_data.split(b'\r\n\r\n', 1)
            header_text = header_bytes.decode('utf-8', errors='ignore')
            first_line = header_text.split('\n')[0]

            # 1. API: Get all messages
            if "GET /api/messages" in first_line:
                conn = sqlite3.connect('chat.db')
                cursor = conn.cursor()
                cursor.execute("SELECT id, sender, recipient, text, media_url, media_type, edited FROM messages")
                rows = cursor.fetchall()
                conn.close()

                messages_list = [{
                    "id": r[0], "sender": r[1], "recipient": r[2], "text": r[3], 
                    "media_url": r[4], "media_type": r[5], "edited": r[6]
                } for r in rows]
                
                json_data = json.dumps(messages_list)
                http_response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + json_data

            # 2. API: Get Online Users
            elif "GET /api/users" in first_line:
                conn = sqlite3.connect('chat.db')
                cursor = conn.cursor()
                threshold = time.time() - 10
                cursor.execute("SELECT username FROM users WHERE last_seen >= ?", (threshold,))
                rows = cursor.fetchall()
                conn.close()
                online_users = [r[0] for r in rows]
                http_response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps(online_users)

            # 3. POST: Login / Register with Case-Insensitive Check
            elif "POST /api/auth" in first_line:
                parsed = parse_qs(body_bytes.decode('utf-8', errors='ignore'))
                username = parsed.get("username", [""])[0].strip()
                password = parsed.get("password", [""])[0].strip()
                lower_username = username.lower()

                response_data = {"status": "error", "message": "Invalid input"}
                if username and password:
                    conn = sqlite3.connect('chat.db')
                    cursor = conn.cursor()
                    
                    cursor.execute("UPDATE users SET lower_username = LOWER(username) WHERE lower_username IS NULL")
                    conn.commit()

                    cursor.execute("SELECT username, password FROM users WHERE lower_username = ?", (lower_username,))
                    row = cursor.fetchone()
                    
                    if row:
                        existing_username, stored_password = row
                        if stored_password == password:
                            cursor.execute("UPDATE users SET last_seen = ? WHERE lower_username = ?", (time.time(), lower_username))
                            conn.commit()
                            response_data = {"status": "success", "message": "Logged in", "username": existing_username}
                        else:
                            response_data = {"status": "error", "message": f"Username '{existing_username}' is already taken with a different password!"}
                    else:
                        try:
                            cursor.execute("INSERT INTO users (username, lower_username, password, last_seen) VALUES (?, ?, ?, ?)", 
                                           (username, lower_username, password, time.time()))
                            conn.commit()
                            response_data = {"status": "success", "message": "Registered and logged in", "username": username}
                        except sqlite3.IntegrityError:
                            response_data = {"status": "error", "message": "This username is already taken!"}
                    conn.close()
                http_response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps(response_data)

            # 4. POST: Heartbeat
            elif "POST /api/heartbeat" in first_line:
                parsed = parse_qs(body_bytes.decode('utf-8', errors='ignore'))
                username = parsed.get("username", [""])[0].strip()
                if username:
                    conn = sqlite3.connect('chat.db')
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET last_seen = ? WHERE lower_username = ?", (time.time(), username.lower()))
                    conn.commit()
                    conn.close()
                http_response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK"

            # 5. POST: Send message
            elif "POST /send" in first_line:
                body_text = body_bytes.decode('utf-8', errors='ignore')
                parsed = parse_qs(body_text)

                sender = parsed.get("sender", ["Anonymous"])[0]
                recipient = parsed.get("recipient", ["Everyone"])[0].strip()
                if not recipient: recipient = "Everyone"
                message_text = parsed.get("message", [""])[0]
                media_url = parsed.get("media_url", [""])[0]
                media_type = parsed.get("media_type", [""])[0]

                if message_text.strip() or media_url:
                    conn = sqlite3.connect('chat.db')
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO messages (sender, recipient, text, media_url, media_type, edited) VALUES (?, ?, ?, ?, ?, 0)",
                        (sender, recipient, message_text, media_url, media_type)
                    )
                    conn.commit()
                    conn.close()

                http_response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK"

            # 6. POST: Delete Message
            elif "POST /delete" in first_line:
                parsed = parse_qs(body_bytes.decode('utf-8', errors='ignore'))
                msg_id = parsed.get("id", [""])[0]
                if msg_id:
                    conn = sqlite3.connect('chat.db')
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
                    conn.commit()
                    conn.close()
                http_response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK"

            # 7. POST: Edit Message
            elif "POST /edit" in first_line:
                parsed = parse_qs(body_bytes.decode('utf-8', errors='ignore'))
                msg_id = parsed.get("id", [""])[0]
                new_text = parsed.get("new_text", [""])[0]
                if msg_id and new_text.strip():
                    conn = sqlite3.connect('chat.db')
                    cursor = conn.cursor()
                    cursor.execute("UPDATE messages SET text = ?, edited = 1 WHERE id = ?", (new_text, msg_id))
                    conn.commit()
                    conn.close()
                http_response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK"

            # 8. POST: Admin Clear Chat (PIN: Yes)
            elif "POST /admin/clear" in first_line:
                parsed = parse_qs(body_bytes.decode('utf-8', errors='ignore'))
                pin = parsed.get("pin", [""])[0]
                if pin == "Yes":
                    conn = sqlite3.connect('chat.db')
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM messages")
                    conn.commit()
                    conn.close()
                    http_response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nSUCCESS"
                else:
                    http_response = "HTTP/1.1 401 Unauthorized\r\nContent-Type: text/plain\r\n\r\nINVALID_PIN"

            # 9. Default GET: Serve Chat UI
            else:
                html_body = generate_chat_html()
                http_response = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + html_body

            client_socket.sendall(http_response.encode('utf-8'))
        except Exception as e:
            print(f"[-] Error: {e}")
        finally:
            client_socket.close()

def generate_chat_html():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Netknock Pro</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <style>
            html, body { 
                background: #111; 
                color: #e9edef; 
                font-family: 'Segoe UI', Tahoma, sans-serif; 
                margin: 0; 
                padding: 0; 
                height: 100%; 
                overflow: hidden; 
            }
            body { 
                display: flex; 
                justify-content: center; 
                align-items: center; 
            }
            .chat-container { 
                width: 100%; 
                max-width: 460px; 
                height: 100dvh; 
                background: #0b141a; 
                display: flex; 
                flex-direction: column; 
                overflow: hidden; 
            }
            
            @media (min-width: 600px) {
                .chat-container { 
                    height: 680px; 
                    border-radius: 12px; 
                    border: 1px solid #222; 
                    box-shadow: 0 8px 24px rgba(0,0,0,0.6); 
                }
            }

            .chat-header { background: #202c33; padding: 12px 15px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #2f3b43; flex-shrink: 0; }
            .chat-header h2 { margin: 0; font-size: 15px; color: #00a884; }
            .admin-btn { background: #2a3942; color: #ff00ff; border: none; padding: 5px 10px; border-radius: 6px; font-size: 11px; cursor: pointer; font-weight: bold; }
            .admin-btn:hover { background: #374248; }

            .mode-selector { background: #111b21; padding: 8px 15px; display: flex; gap: 8px; border-bottom: 1px solid #2f3b43; align-items: center; font-size: 12px; flex-shrink: 0; }
            .mode-selector select { background: #2a3942; border: none; color: #fff; padding: 6px 10px; border-radius: 6px; outline: none; font-size: 12px; flex: 1; }

            .online-users-bar { background: #1f2c34; padding: 6px 15px; display: flex; gap: 6px; overflow-x: auto; border-bottom: 1px solid #2f3b43; flex-shrink: 0; align-items: center; }
            .online-users-bar::-webkit-scrollbar { height: 3px; }
            .online-users-bar::-webkit-scrollbar-thumb { background: #00a884; border-radius: 3px; }
            .online-chip { background: #2a3942; color: #00a884; padding: 3px 8px; border-radius: 12px; font-size: 11px; cursor: pointer; white-space: nowrap; border: 1px solid transparent; }
            .online-chip:hover { border-color: #00a884; }
            .online-chip.active { background: #00a884; color: #fff; }

            .user-setup { background: #1f2c34; padding: 20px 15px; display: flex; flex-direction: column; gap: 10px; justify-content: center; height: 100%; box-sizing: border-box; text-align: center; }
            .user-setup input { padding: 10px 14px; background: #2a3942; border: none; color: #fff; border-radius: 8px; font-size: 14px; outline: none; width: 100%; box-sizing: border-box; }
            
            .chat-box { 
                flex: 1; 
                min-height: 0; 
                background-color: #0b141a; 
                background-image: radial-gradient(#111b21 1px, transparent 1px); 
                background-size: 20px 20px; 
                padding: 15px; 
                overflow-y: auto; 
                display: flex; 
                flex-direction: column; 
                gap: 8px; 
            }
            
            .message-row { display: flex; align-items: flex-end; gap: 8px; max-width: 85%; position: relative; }
            .message-row.sent { align-self: flex-end; flex-direction: row-reverse; }
            .message-row.received { align-self: flex-start; }
            
            .avatar { width: 26px; height: 26px; border-radius: 50%; background: #00a884; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 10px; flex-shrink: 0; text-transform: uppercase; }
            
            .bubble { padding: 8px 32px 8px 12px; border-radius: 8px; font-size: 13px; word-break: break-word; position: relative; line-height: 1.4; box-shadow: 0 1px 0.5px rgba(0,0,0,0.3); min-width: 80px; }
            .sent .bubble { background: #005c4b; color: #e9edef; border-bottom-right-radius: 0; }
            .received .bubble { background: #202c33; color: #e9edef; border-bottom-left-radius: 0; }
            
            .sender-name { font-size: 11px; font-weight: bold; color: #53bdeb; margin-bottom: 2px; }
            .dm-badge { font-size: 10px; background: rgba(255,0,255,0.2); color: #ff00ff; padding: 2px 5px; border-radius: 4px; margin-bottom: 3px; display: inline-block; }
            .edited-tag { font-size: 9px; color: #8696a0; float: right; margin-left: 6px; margin-top: 4px; }

            .msg-menu-container { position: absolute; right: 6px; top: 6px; z-index: 10; display: inline-block; }
            .dots-btn { background: none; border: none; color: #b1cbd0; cursor: pointer; font-size: 15px; padding: 0 4px; font-weight: bold; }
            .dots-btn:hover { color: #fff; }
            .dropdown-menu { display: none; position: absolute; right: 0; top: 22px; background: #233138; border: 1px solid #2f3b43; border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.6); z-index: 20; width: 95px; }
            .dropdown-menu button { background: none; border: none; color: #e9edef; width: 100%; text-align: left; padding: 7px 10px; font-size: 12px; cursor: pointer; }
            .dropdown-menu button:hover { background: #182229; color: #00a884; }

            img.chat-media, video.chat-media { max-width: 220px; max-height: 180px; border-radius: 6px; margin-top: 4px; display: block; object-fit: cover; }
            audio.chat-media { width: 200px; height: 35px; margin-top: 4px; }

            .chat-footer { 
                flex-shrink: 0; 
                background: #202c33; 
                padding: 10px 15px; 
                display: flex; 
                flex-direction: column; 
                gap: 8px; 
                border-top: 1px solid #2f3b43; 
            }
            .input-row { display: flex; gap: 6px; align-items: center; }
            .chat-footer input[type="text"] { flex: 1; padding: 9px 12px; background: #2a3942; border: none; color: #fff; border-radius: 8px; font-size: 13px; outline: none; }
            .chat-footer input[type="file"] { display: none; }
            
            .action-btn { background: #2a3942; color: #00a884; padding: 8px 10px; border-radius: 8px; cursor: pointer; font-size: 14px; text-align: center; border: none; display: inline-flex; align-items: center; justify-content: center; }
            .action-btn:hover { background: #374248; }
            .action-btn.recording { background: #ff4444; color: #fff; animation: pulse 1.2s infinite; }

            @keyframes pulse {
                0% { opacity: 1; }
                50% { opacity: 0.5; }
                100% { opacity: 1; }
            }

            button.send-btn { background: #00a884; color: #fff; border: none; padding: 9px 14px; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 13px; }
            button.send-btn:hover { background: #019576; }
            
            .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); justify-content: center; align-items: center; z-index: 100; }
            .modal-box { background: #1f2c34; padding: 20px; border-radius: 10px; width: 300px; border: 1px solid #2f3b43; text-align: center; }
            .modal-box input { width: 100%; padding: 8px; background: #2a3942; border: none; color: #fff; border-radius: 6px; margin: 10px 0; outline: none; box-sizing: border-box; }
            
            a { color: #53bdeb; text-decoration: none; }
        </style>
    </head>
    <body onclick="closeAllDropdowns(event)">
        <div class="chat-container" onclick="event.stopPropagation()">
            <div class="chat-header">
                <h2>Netknock Pro</h2>
                <div style="display: flex; gap: 8px; align-items: center;">
                    <button class="admin-btn" onclick="openAdminModal()"> Owner PIN</button>
                    <div id="greeting" style="display:none; font-size: 12px; color: #8696a0;">
                        <span id="current-user" style="color: #00a884; font-weight: bold;"></span> 
                        (<a href="#" onclick="logout()">Switch</a>)
                    </div>
                </div>
            </div>
            
            <!-- SECURE LOGIN SCREEN -->
            <div class="user-setup" id="setup-box">
                <h3 style="color: #00a884; margin-bottom: 5px;">Welcome to Netknock Pro</h3>
                <p style="font-size: 12px; color: #8696a0; margin-top: 0;">Enter your username & password:</p>
                <input type="text" id="username-input" placeholder="Username..." autocomplete="off">
                <input type="password" id="password-input" placeholder="Password..." autocomplete="off">
                <button class="send-btn" onclick="loginUser()">Login / Register</button>
                <div id="auth-error" style="color: #ff4444; font-size: 12px; display:none;"></div>
            </div>

            <div class="mode-selector" id="mode-bar" style="display:none;">
                <span style="color:#8696a0;">Mode:</span>
                <select id="chat-mode" onchange="toggleMode()">
                    <option value="Everyone"> Public Group</option>
                    <option value="DM"> Direct Message</option>
                </select>
            </div>

            <!-- ONLINE USERS CHIPS FOR DM CLICK-TO-TEXT -->
            <div class="online-users-bar" id="online-users-bar" style="display:none;">
                <span style="font-size: 11px; color: #8696a0; white-space: nowrap;">Online:</span>
                <div id="online-chips-container" style="display: flex; gap: 6px;"></div>
            </div>

            <div class="chat-box" id="chat-box" style="display:none;">
                <p style='color: #8696a0; text-align: center; font-size: 12px; margin-top: 50px;'>Loading messages...</p>
            </div>

            <form class="chat-footer" id="chat-form" onsubmit="sendMessage(event)" style="display:none;">
                <div class="input-row">
                    <label class="action-btn" title="Attach Any File (Max 200MB)">
                        File<input type="file" id="media-file" accept="*" onchange="previewFile()">
                    </label>
                    <button type="button" class="action-btn" id="mic-btn" title="Click to Record/Stop Voice Note" onclick="toggleVoiceRecording()">
                        Rec
                    </button>
                    <input type="text" id="message-input" placeholder="Type a message..." autocomplete="off">
                    <button type="submit" class="send-btn">Send</button>
                </div>
                <div id="file-preview" style="font-size: 11px; color: #00a884; display:none;"></div>
            </form>
        </div>

        <div class="modal-overlay" id="admin-modal">
            <div class="modal-box">
                <h3 style="color: #ff00ff; margin-top: 0;">Owner Control Panel</h3>
                <p style="font-size: 12px; color: #8696a0;">Enter Owner PIN to manage server storage:</p>
                <input type="password" id="admin-pin-input" placeholder="Enter PIN..." autocomplete="off">
                <div style="display: flex; gap: 8px;">
                    <button class="send-btn" style="background: #ff4444; flex:1;" onclick="executeClearChat()">Wipe Chat DB</button>
                    <button class="action-btn" style="flex:1;" onclick="closeAdminModal()">Cancel</button>
                </div>
            </div>
        </div>

        <script>
            let myName = sessionStorage.getItem('chat_username');
            let selectedDmPartner = null;
            let allMessages = [];
            let lastRenderedState = "";
            let selectedFileBase64 = null;
            let selectedFileType = null;

            let mediaRecorder = null;
            let audioChunks = [];
            let isRecording = false;

            if (myName) {
                showChatInterface();
            }

            async function loginUser() {
                let username = document.getElementById('username-input').value.trim();
                let password = document.getElementById('password-input').value.trim();
                let errorDiv = document.getElementById('auth-error');

                if (!username || !password) {
                    errorDiv.innerText = "Please enter both username and password.";
                    errorDiv.style.display = 'block';
                    return;
                }

                let formData = new URLSearchParams();
                formData.append('username', username);
                formData.append('password', password);

                try {
                    let res = await fetch('/api/auth', { method: 'POST', body: formData });
                    let data = await res.json();
                    if (data.status === 'success') {
                        sessionStorage.setItem('chat_username', data.username);
                        myName = data.username;
                        showChatInterface();
                    } else {
                        errorDiv.innerText = data.message;
                        errorDiv.style.display = 'block';
                    }
                } catch (err) {
                    errorDiv.innerText = "Connection error during login.";
                    errorDiv.style.display = 'block';
                }
            }

            function showChatInterface() {
                document.getElementById('setup-box').style.display = 'none';
                document.getElementById('greeting').style.display = 'block';
                document.getElementById('current-user').innerText = myName;
                document.getElementById('mode-bar').style.display = 'flex';
                document.getElementById('chat-box').style.display = 'flex';
                document.getElementById('chat-form').style.display = 'flex';
                fetchMessages();
                fetchOnlineUsers();
            }

            function logout() {
                sessionStorage.removeItem('chat_username');
                location.reload();
            }

            function toggleMode() {
                let mode = document.getElementById('chat-mode').value;
                let onlineBar = document.getElementById('online-users-bar');
                if (mode === 'DM') {
                    onlineBar.style.display = 'flex';
                } else {
                    onlineBar.style.display = 'none';
                    selectedDmPartner = null;
                }
                lastRenderedState = "";
                renderChat();
            }

            function selectDmPartner(user) {
                selectedDmPartner = user;
                lastRenderedState = "";
                renderChat();
            }

            function previewFile() {
                let fileInput = document.getElementById('media-file');
                let preview = document.getElementById('file-preview');
                
                if (fileInput.files.length > 0) {
                    let file = fileInput.files[0];
                    const maxSize = 200 * 1024 * 1024; // 200MB limit
                    
                    if (file.size > maxSize) {
                        alert("File is too large! Maximum limit is 200MB.");
                        fileInput.value = '';
                        preview.style.display = 'none';
                        return;
                    }

                    let reader = new FileReader();
                    reader.onload = function(e) {
                        selectedFileBase64 = e.target.result;
                        if (file.type.startsWith('image')) {
                            selectedFileType = 'image';
                        } else if (file.type.startsWith('video')) {
                            selectedFileType = 'video';
                        } else if (file.type.startsWith('audio')) {
                            selectedFileType = 'audio';
                        } else {
                            selectedFileType = 'image';
                        }
                        preview.style.display = 'block';
                        preview.innerText = '📎 Attached: ' + file.name;
                    };
                    reader.readAsDataURL(file);
                } else {
                    preview.style.display = 'none';
                    selectedFileBase64 = null;
                    selectedFileType = null;
                }
            }

            // TAP-TO-RECORD / TAP-TO-STOP VOICE NOTE FIX
            async function toggleVoiceRecording() {
                let micBtn = document.getElementById('mic-btn');
                let preview = document.getElementById('file-preview');

                if (!isRecording) {
                    try {
                        let stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                        mediaRecorder = new MediaRecorder(stream);
                        audioChunks = [];

                        mediaRecorder.ondataavailable = event => {
                            if (event.data.size > 0) {
                                audioChunks.push(event.data);
                            }
                        };

                        mediaRecorder.onstop = () => {
                            let audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                            let reader = new FileReader();
                            reader.onload = function(e) {
                                selectedFileBase64 = e.target.result;
                                selectedFileType = 'audio';
                                preview.style.display = 'block';
                                preview.innerText = ' Voice Note Recorded & Ready to Send';
                            };
                            reader.readAsDataURL(audioBlob);
                        };

                        mediaRecorder.start();
                        isRecording = true;
                        micBtn.classList.add('recording');
                        preview.style.display = 'block';
                        preview.innerText = ' Recording... Click mic again to stop.';
                    } catch (err) {
                        alert("Microphone error: Make sure your browser allows microphone permissions (Note: Some browsers block mics on unsecured non-HTTPS connections).");
                    }
                } else {
                    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                        mediaRecorder.stop();
                        mediaRecorder.stream.getTracks().forEach(track => track.stop());
                    }
                    isRecording = false;
                    micBtn.classList.remove('recording');
                }
            }

            async function fetchMessages() {
                try {
                    let response = await fetch('/api/messages');
                    allMessages = await response.json();
                    renderChat();
                } catch (err) {
                    console.error("Failed to fetch messages");
                }
            }

            async function fetchOnlineUsers() {
                if (!myName) return;
                try {
                    let hbData = new URLSearchParams();
                    hbData.append('username', myName);
                    await fetch('/api/heartbeat', { method: 'POST', body: hbData });

                    let res = await fetch('/api/users');
                    let users = await res.json();
                    
                    let container = document.getElementById('online-chips-container');
                    let html = '';
                    users.forEach(u => {
                        if (u.toLowerCase() !== myName.toLowerCase()) {
                            let activeClass = (selectedDmPartner && selectedDmPartner.toLowerCase() === u.toLowerCase()) ? 'active' : '';
                            html += `<div class="online-chip ${activeClass}" onclick="selectDmPartner('${u}')">🟢 @${u}</div>`;
                        }
                    });
                    if (!html) {
                        html = '<span style="font-size:11px; color:#8696a0;">No other users online</span>';
                    }
                    container.innerHTML = html;
                } catch (err) {
                    console.error("Failed to fetch online users");
                }
            }

            function toggleDropdown(id, event) {
                event.stopPropagation();
                document.querySelectorAll('.dropdown-menu').forEach(el => {
                    if (el.id !== 'menu-' + id) el.style.display = 'none';
                });
                let menu = document.getElementById('menu-' + id);
                if (menu) {
                    menu.style.display = (menu.style.display === 'block') ? 'none' : 'block';
                }
            }

            function closeAllDropdowns(event) {
                document.querySelectorAll('.dropdown-menu').forEach(el => {
                    el.style.display = 'none';
                });
            }

            function renderChat() {
                let chatBox = document.getElementById('chat-box');
                
                let audioElements = chatBox.querySelectorAll('audio');
                for (let audio of audioElements) {
                    if (!audio.paused) { return; }
                }

                let mode = document.getElementById('chat-mode').value;

                let filtered = allMessages.filter(m => {
                    let sender = (m.sender || "").toLowerCase();
                    let recipient = (m.recipient || "everyone").toLowerCase();
                    
                    if (mode === 'Everyone') {
                        return recipient === 'everyone';
                    } else {
                        if (!selectedDmPartner) return false;
                        let partner = selectedDmPartner.toLowerCase();
                        return (sender === myName.toLowerCase() && recipient === partner) ||
                               (sender === partner && recipient === myName.toLowerCase());
                    }
                });

                let currentStateKey = JSON.stringify(filtered) + "_" + mode + "_" + selectedDmPartner;
                if (currentStateKey === lastRenderedState) return;
                lastRenderedState = currentStateKey;

                if (filtered.length === 0) {
                    let msg = mode === 'DM' && !selectedDmPartner ? "Select an online user above to start a Direct Message." : "No messages here yet.";
                    chatBox.innerHTML = `<p style='color: #8696a0; text-align: center; font-size: 12px; margin-top: 50px;'>${msg}</p>`;
                    return;
                }

                let html = '';
                filtered.forEach(m => {
                    let isMe = (m.sender.toLowerCase() === myName.toLowerCase());
                    let alignmentClass = isMe ? 'sent' : 'received';
                    let initial = m.sender ? m.sender.charAt(0) : '?';
                    let senderLabel = !isMe ? `<div class="sender-name">${m.sender}</div>` : '';
                    
                    let dmBadge = '';
                    if (m.recipient && m.recipient.toLowerCase() !== 'everyone') {
                        dmBadge = `<div class="dm-badge"> DM to @${m.recipient}</div>`;
                    }

                    let mediaHtml = '';
                    if (m.media_url && m.media_type === 'image') {
                        mediaHtml = `<img src="${m.media_url}" class="chat-media">`;
                    } else if (m.media_url && m.media_type === 'video') {
                        mediaHtml = `<video src="${m.media_url}" controls class="chat-media"></video>`;
                    } else if (m.media_url && m.media_type === 'audio') {
                        mediaHtml = `<audio src="${m.media_url}" controls class="chat-media"></audio>`;
                    }

                    let textHtml = m.text ? `<div>${m.text}</div>` : '';
                    let editedTag = m.edited ? `<span class="edited-tag">(edited)</span>` : '';

                    let actionsHtml = '';
                    if (isMe) {
                        actionsHtml = `
                        <div class="msg-menu-container">
                            <button class="dots-btn" onclick="toggleDropdown(${m.id}, event)">@</button>
                            <div class="dropdown-menu" id="menu-${m.id}">
                                <button onclick="editMessage(${m.id}, '${btoa(m.text || '')}')"> Edit</button>
                                <button onclick="deleteMessage(${m.id})"> Delete</button>
                            </div>
                        </div>`;
                    }

                    html += `
                    <div class="message-row ${alignmentClass}">
                        <div class="avatar">${initial}</div>
                        <div class="bubble">
                            ${actionsHtml}
                            ${senderLabel}
                            ${dmBadge}
                            ${textHtml}
                            ${mediaHtml}
                            ${editedTag}
                        </div>
                    </div>`;
                });
                
                chatBox.innerHTML = html;
                chatBox.scrollTop = chatBox.scrollHeight;
            }

            async function sendMessage(event) {
                event.preventDefault();
                let message = document.getElementById('message-input').value;
                let mode = document.getElementById('chat-mode').value;

                let recipient = "Everyone";
                if (mode === 'DM') {
                    if (!selectedDmPartner) {
                        alert("Please select an online user to message first!");
                        return;
                    }
                    recipient = selectedDmPartner;
                }

                let formData = new URLSearchParams();
                formData.append('username', myName);
                formData.append('sender', myName);
                formData.append('recipient', recipient);
                formData.append('message', message);

                if (selectedFileBase64 && selectedFileType) {
                    formData.append('media_url', selectedFileBase64);
                    formData.append('media_type', selectedFileType);
                }

                await fetch('/send', { method: 'POST', body: formData });

                document.getElementById('message-input').value = '';
                document.getElementById('media-file').value = '';
                selectedFileBase64 = null;
                selectedFileType = null;
                document.getElementById('file-preview').style.display = 'none';
                fetchMessages();
            }

            async function deleteMessage(id) {
                if (confirm("Delete this message?")) {
                    let formData = new URLSearchParams();
                    formData.append('id', id);
                    await fetch('/delete', { method: 'POST', body: formData });
                    lastRenderedState = "";
                    fetchMessages();
                }
            }

            async function editMessage(id, encodedText) {
                let oldText = atob(encodedText);
                let newText = prompt("Edit your message:", oldText);
                if (newText !== null && newText.trim() !== oldText) {
                    let formData = new URLSearchParams();
                    formData.append('id', id);
                    formData.append('new_text', newText);
                    await fetch('/edit', { method: 'POST', body: formData });
                    lastRenderedState = "";
                    fetchMessages();
                }
            }

            function openAdminModal() {
                document.getElementById('admin-modal').style.display = 'flex';
            }

            function closeAdminModal() {
                document.getElementById('admin-modal').style.display = 'none';
                document.getElementById('admin-pin-input').value = '';
            }

            async function executeClearChat() {
                let pin = document.getElementById('admin-pin-input').value;
                let formData = new URLSearchParams();
                formData.append('pin', pin);

                let res = await fetch('/admin/clear', { method: 'POST', body: formData });
                if (res.ok) {
                    alert("Chat database successfully wiped!");
                    closeAdminModal();
                    lastRenderedState = "";
                    fetchMessages();
                } else {
                    alert("Incorrect Owner PIN!");
                }
            }

            setInterval(() => {
                fetchMessages();
                fetchOnlineUsers();
            }, 2000);
            
            if (myName) {
                fetchOnlineUsers();
            }
        </script>
    </body>
    </html>
    """

if __name__ == '__main__':
    run_server()
