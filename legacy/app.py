from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Temporary memory storage for incoming real-time evidence dockets from the Pi
complaints_db = []

@app.route('/')
def index():
    return render_template('index.html')

# API Endpoint where the Raspberry Pi edge node pushes data
@app.route('/api/complaints', methods=['GET', 'POST'])
def handle_complaints():
    if request.method == 'POST':
        data = request.json
        if data:
            complaints_db.append(data)
            print(f"[LIVE RECEIVED] Plate: {data.get('licensePlate')} | Action: {data.get('vehicleType', 'Car Slowing Down')}")
            return jsonify({"message": "Success"}), 201
        return jsonify({"error": "Invalid data"}), 400
    
    # When the website fetches updates every 2 seconds, return the list
    return jsonify(complaints_db)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)