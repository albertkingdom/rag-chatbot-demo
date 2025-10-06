
document.addEventListener('DOMContentLoaded', () => {
    const messageInput = document.getElementById('message-input');
    const sendButton = document.getElementById('send-button');
    const chatWindow = document.getElementById('chat-window');

    const fileInput = document.getElementById('file-input');
    const uploadButton = document.getElementById('upload-button');
    const resultsContent = document.getElementById('results-content');

    // --- Chatbot Logic ---
    const sendMessage = async () => {
        const message = messageInput.value.trim();
        if (!message) return;

        // Display user message
        appendMessage(message, 'user');
        messageInput.value = '';

        // Send message to backend
        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ message: message })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            appendMessage(data.response, 'assistant');

        } catch (error) {
            console.error('Error sending message:', error);
            appendMessage('Sorry, something went wrong. Please try again.', 'assistant');
        }
    };

    const appendMessage = (message, sender) => {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', `${sender}-message`);
        
        const p = document.createElement('p');
        p.textContent = message;
        messageElement.appendChild(p);
        
        chatWindow.appendChild(messageElement);
        chatWindow.scrollTop = chatWindow.scrollHeight; // Auto-scroll to bottom
    };

    sendButton.addEventListener('click', sendMessage);
    messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });

    // --- BOM Mapper Logic ---
    const uploadAndMapFile = async () => {
        const file = fileInput.files[0];
        if (!file) {
            resultsContent.textContent = 'Please select a file first.';
            return;
        }

        resultsContent.textContent = 'Uploading and processing...';
        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch('/map_bom', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || `HTTP error! status: ${response.status}`);
            }

            // Format and display the JSON results
            resultsContent.textContent = JSON.stringify(data, null, 4);

        } catch (error) {
            console.error('Error uploading file:', error);
            resultsContent.textContent = `Error: ${error.message}`;
        }
    };

    uploadButton.addEventListener('click', uploadAndMapFile);
});
