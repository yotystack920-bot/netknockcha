import socket
import sqlite3
import json
from urllib.parse import parse_qs

# --- ROBUST DATABASE SETUP ---
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
    conn.commit()
    conn.close()

def run_server():
    init_db()
    HOST = '0.0.0.0'
    PORT = 8080

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(20)
    
    print(f"[*] Netknock Pro Server running at http://{HOST}:{PORT}")

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
            print(f"[+] Request: {first_line}")

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
                http_response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n\r\n"
                    + json_data
                )

            # 2. POST: Send message with Base64 Media / Audio Upload
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

            # 3. POST: Delete Message
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

            # 4. POST: Edit Message
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

            # 5. POST: Admin Clear Chat (PIN: yo200st!)
            elif "POST /admin/clear" in first_line:
                parsed = parse_qs(body_bytes.decode('utf-8', errors='ignore'))
                pin = parsed.get("pin", [""])[0]
                if pin == "yo200st!":
                    conn = sqlite3.connect('chat.db')
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM messages")
                    conn.commit()
                    conn.close()
                    http_response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nSUCCESS"
                else:
                    http_response = "HTTP/1.1 401 Unauthorized\r\nContent-Type: text/plain\r\n\r\nINVALID_PIN"

            # 6. Default GET: Serve Chat UI
            else:
                html_body = generate_chat_html()
                http_response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html\r\n\r\n"
                    + html_body
                )

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
            .mode-selector select, .mode-selector input { background: #2a3942; border: none; color: #fff; padding: 6px 10px; border-radius: 6px; outline: none; font-size: 12px; flex: 1; }

            .user-setup { background: #1f2c34; padding: 10px 15px; display: flex; gap: 8px; align-items: center; border-bottom: 1px solid #2f3b43; flex-shrink: 0; }
            .user-setup input { flex: 1; padding: 8px 12px; background: #2a3942; border: none; color: #fff; border-radius: 8px; font-size: 13px; outline: none; }
            
            /* CRITICAL FIX: flex: 1 and min-height: 0 ensures only this box scrolls */
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
            
            .bubble { padding: 8px 12px; border-radius: 8px; font-size: 13px; word-break: break-word; position: relative; line-height: 1.4; box-shadow: 0 1px 0.5px rgba(0,0,0,0.3); min-width: 80px; }
            .sent .bubble { background: #005c4b; color: #e9edef; border-bottom-right-radius: 0; }
            .received .bubble { background: #202c33; color: #e9edef; border-bottom-left-radius: 0; }
            
            .sender-name { font-size: 11px; font-weight: bold; color: #53bdeb; margin-bottom: 2px; }
            .dm-badge { font-size: 10px; background: rgba(255,0,255,0.2); color: #ff00ff; padding: 2px 5px; border-radius: 4px; margin-bottom: 3px; display: inline-block; }
            .edited-tag { font-size: 9px; color: #8696a0; float: right; margin-left: 6px; margin-top: 4px; }

            .msg-actions { display: none; position: absolute; right: 5px; top: 2px; background: rgba(0,0,0,0.6); border-radius: 4px; padding: 2px; }
            .message-row:hover .msg-actions { display: flex; gap: 4px; }
            .msg-actions button { background: none; border: none; color: #aaa; cursor: pointer; font-size: 11px; padding: 2px 4px; }
            .msg-actions button:hover { color: #fff; }

            img.chat-media, video.chat-media { max-width: 220px; max-height: 180px; border-radius: 6px; margin-top: 4px; display: block; object-fit: cover; }
            audio.chat-media { width: 200px; height: 35px; margin-top: 4px; }

            /* CRITICAL FIX: flex-shrink: 0 forces footer to stay completely locked at bottom */
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

            button.send-btn { background: #00a884; color: #fff; border: none; padding: 9px 14px; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 13px; }
            button.send-btn:hover { background: #019576; }
            
            .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); justify-content: center; align-items: center; z-index: 100; }
            .modal-box { background: #1f2c34; padding: 20px; border-radius: 10px; width: 300px; border: 1px solid #2f3b43; text-align: center; }
            .modal-box input { width: 100%; padding: 8px; background: #2a3942; border: none; color: #fff; border-radius: 6px; margin: 10px 0; outline: none; box-sizing: border-box; }
            
            a { color: #53bdeb; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="chat-container">
            <div class="chat-header">
                <h2>Netknock Pro</h2>
                <div style="display: flex; gap: 8px; align-items: center;">
                    <button class="admin-btn" onclick="openAdminModal()"> Owner PIN</button>
                    <div id="greeting" style="display:none; font-size: 12px; color: #8696a0;">
                        <span id="current-user" style="color: #00a884; font-weight: bold;"></span> 
                        (<a href="#" onclick="changeName()">Switch</a>)
                    </div>
                </div>
            </div>
            
            <div class="user-setup" id="setup-box">
                <input type="text" id="username-input" placeholder="Enter your display name..." autocomplete="off">
                <button class="send-btn" onclick="saveUsername()">Start</button>
            </div>

            <div class="mode-selector" id="mode-bar" style="display:none;">
                <span style="color:#8696a0;">Mode:</span>
                <select id="chat-mode" onchange="renderChat()">
                    <option value="Everyone"> Public Group</option>
                    <option value="DM"> Direct Message</option>
                </select>
                <input type="text" id="dm-recipient" placeholder="DM partner..." style="display:none;" autocomplete="off">
            </div>

            <div class="chat-box" id="chat-box">
                <p style='color: #8696a0; text-align: center; font-size: 12px; margin-top: 50px;'>Loading messages...</p>
            </div>

            <form class="chat-footer" id="chat-form" onsubmit="sendMessage(event)" style="display:none;">
                <div class="input-row">
                    <label class="action-btn" title="Attach Photo/Video">
                        Pic<input type="file" id="media-file" accept="image/*,video/*" onchange="previewFile()">
                    </label>
                    <label class="action-btn" title="Upload Audio File from PC">
                        Mus<input type="file" id="audio-file" accept="audio/*" onchange="previewAudioFile()">
                    </label>
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
            let allMessages = [];
            let selectedFileBase64 = null;
            let selectedFileType = null;

            if (myName) {
                document.getElementById('setup-box').style.display = 'none';
                document.getElementById('greeting').style.display = 'block';
                document.getElementById('current-user').innerText = myName;
                document.getElementById('mode-bar').style.display = 'flex';
                document.getElementById('chat-form').style.display = 'flex';
            }

            document.getElementById('chat-mode').addEventListener('change', function() {
                let dmInput = document.getElementById('dm-recipient');
                dmInput.style.display = (this.value === 'DM') ? 'block' : 'none';
                renderChat();
            });

            document.getElementById('dm-recipient').addEventListener('input', renderChat);

            function saveUsername() {
                let name = document.getElementById('username-input').value.trim();
                if (name) {
                    sessionStorage.setItem('chat_username', name);
                    location.reload();
                }
            }

            function changeName() {
                sessionStorage.removeItem('chat_username');
                location.reload();
            }

            function previewFile() {
                let fileInput = document.getElementById('media-file');
                let preview = document.getElementById('file-preview');
                document.getElementById('audio-file').value = ''; 
                
                if (fileInput.files.length > 0) {
                    let file = fileInput.files[0];
                    let reader = new FileReader();
                    reader.onload = function(e) {
                        selectedFileBase64 = e.target.result;
                        selectedFileType = file.type.startsWith('video') ? 'video' : 'image';
                        preview.style.display = 'block';
                        preview.innerText = 'Attached: ' + file.name;
                    };
                    reader.readAsDataURL(file);
                } else {
                    preview.style.display = 'none';
                    selectedFileBase64 = null;
                    selectedFileType = null;
                }
            }

            function previewAudioFile() {
                let fileInput = document.getElementById('audio-file');
                let preview = document.getElementById('file-preview');
                document.getElementById('media-file').value = ''; 

                if (fileInput.files.length > 0) {
                    let file = fileInput.files[0];
                    let reader = new FileReader();
                    reader.onload = function(e) {
                        selectedFileBase64 = e.target.result;
                        selectedFileType = 'audio';
                        preview.style.display = 'block';
                        preview.innerText = ' Audio Attached: ' + file.name;
                    };
                    reader.readAsDataURL(file);
                } else {
                    preview.style.display = 'none';
                    selectedFileBase64 = null;
                    selectedFileType = null;
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

            function renderChat() {
                let chatBox = document.getElementById('chat-box');
                
                let audioElements = chatBox.querySelectorAll('audio');
                for (let audio of audioElements) {
                    if (!audio.paused) {
                        return; 
                    }
                }

                let mode = document.getElementById('chat-mode').value;
                let dmPartner = document.getElementById('dm-recipient').value.trim().toLowerCase();

                let filtered = allMessages.filter(m => {
                    let sender = (m.sender || "").toLowerCase();
                    let recipient = (m.recipient || "everyone").toLowerCase();
                    
                    if (mode === 'Everyone') {
                        return recipient === 'everyone';
                    } else {
                        if (!dmPartner) return false;
                        return (sender === myName.toLowerCase() && recipient === dmPartner) ||
                               (sender === dmPartner && recipient === myName.toLowerCase());
                    }
                });

                if (filtered.length === 0) {
                    chatBox.innerHTML = "<p style='color: #8696a0; text-align: center; font-size: 12px; margin-top: 50px;'>No messages here yet.</p>";
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
                        <div class="msg-actions">
                            <button onclick="editMessage(${m.id}, '${btoa(m.text || '')}')" title="Edit"></button>
                            <button onclick="deleteMessage(${m.id})" title="Delete"></button>
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
                
                if (chatBox.innerHTML !== html) {
                    chatBox.innerHTML = html;
                    chatBox.scrollTop = chatBox.scrollHeight;
                }
            }

            async function sendMessage(event) {
                event.preventDefault();
                let message = document.getElementById('message-input').value;
                let mode = document.getElementById('chat-mode').value;
                let dmPartner = document.getElementById('dm-recipient').value.trim();

                let recipient = "Everyone";
                if (mode === 'DM' && dmPartner) {
                    recipient = dmPartner;
                }

                let formData = new URLSearchParams();
                formData.append('sender', myName);
                formData.append('recipient', recipient);
                formData.append('message', message);

                if (selectedFileBase64 && selectedFileType) {
                    formData.append('media_url', selectedFileBase64);
                    formData.append('media_type', selectedFileType);
                }

                await fetch('/send', {
                    method: 'POST',
                    body: formData
                });

                document.getElementById('message-input').value = '';
                document.getElementById('media-file').value = '';
                document.getElementById('audio-file').value = '';
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
                    fetchMessages();
                } else {
                    alert("Incorrect Owner PIN!");
                }
            }

            setInterval(fetchMessages, 2000);
            fetchMessages();
        </script>
    </body>
    </html>
    """

if __name__ == '__main__':
    run_server()