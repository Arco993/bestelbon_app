from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "<h1>Het begin van je Bestelsysteem!</h1><p>De setup is gelukt.</p>"

if __name__ == '__main__':
    app.run(debug=True)