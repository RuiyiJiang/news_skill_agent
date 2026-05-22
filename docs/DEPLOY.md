# LLM摘要功能部署指南

## 问题原因

前端页面调用 `/api/summarize` 生成摘要时失败，通常是因为：

1. **API 服务器没有运行**
2. **前端和 API 不在同一域名/端口**，导致跨域问题

## 解决方案

### 方案一：同一服务器部署（推荐）

将前端静态文件和后端 API 部署在同一服务器上。

**Nginx 配置示例：**

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 前端静态文件
    location / {
        root /path/to/docs;
        index index.html;
    }

    # API 代理到后端
    location /api/ {
        proxy_pass http://localhost:5000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

然后启动后端：
```bash
cd /path/to/project
python start_api_server.py --port 5000
```

### 方案二：分离部署

如果前端和后端分开部署，需要修改前端配置：

1. 编辑 `docs/index.html`，修改 `API_BASE_URL`：

```javascript
const API_BASE_URL = 'http://your-api-server:5000';  // 或 https://api.yourdomain.com
```

2. 确保后端允许跨域（已默认开启 CORS）

3. 启动后端服务器：
```bash
python start_api_server.py --host 0.0.0.0 --port 5000
```

### 方案三：本地测试

本地开发时，可以分别启动：

**终端1 - 启动后端：**
```bash
python start_api_server.py --port 5000
```

**终端2 - 启动前端（使用 Python 简单 HTTP 服务器）：**
```bash
cd docs
python -m http.server 8080
```

然后修改 `API_BASE_URL`：
```javascript
const API_BASE_URL = 'http://localhost:5000';
```

访问 `http://localhost:8080` 即可测试。

## 检查清单

部署前请确认：

- [ ] 后端服务器已启动（`python start_api_server.py`）
- [ ] `.env` 文件中配置了正确的 `OPENAI_API_KEY`
- [ ] 前端 `API_BASE_URL` 配置正确
- [ ] 防火墙/安全组允许 API 端口访问（如果是分离部署）

## 故障排查

### 浏览器控制台显示 "Failed to fetch"
- 检查后端是否运行
- 检查 API 地址是否正确
- 检查是否有跨域问题

### 显示 "生成摘要失败"
- 检查后端日志是否有错误
- 确认 `.env` 中 `OPENAI_API_KEY` 已配置
- 确认 API 余额充足

### 后端启动失败
- 检查端口是否被占用
- 检查依赖是否安装：`pip install -r requirements.txt`
