"""启动开发服务器：uv run main.py"""

import uvicorn


def main():
    # 0.0.0.0：允许同一局域网内的手机/其他设备访问
    # 暴露到公网前请在 .env 里设置 GM_PASSWORD（见 README「公网访问」）
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
