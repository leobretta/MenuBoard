from flask import Flask, render_template

app = Flask(__name__)

@app.route("/tv1")
def tv1():
    return render_template("tv1.html")

@app.route("/tv2")
def tv2():
    return render_template("tv2.html")

if __name__ == "__main__":
    app.run(debug=True)