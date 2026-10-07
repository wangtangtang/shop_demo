from app import create_app

app = create_app()

if __name__ == "__main__":
    # 0.0.0.0 表示监听所有网卡，方便外部访问
    app.run(host="0.0.0.0", port=5000, debug=True)
