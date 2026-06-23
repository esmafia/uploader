import threading
from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "I am alive"


def keep_alive():
    t = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8090))
    t.daemon = True
    t.start()
