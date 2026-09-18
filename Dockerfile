# ---- 构建依赖（安装所有包）----
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.txt


# ---- 运行时 ----
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/venv/bin:$PATH

# OCR 依赖：tesseract + 中文语言包 + 运行时共享库（chromadb 需要 libgomp1）
# antiword：老式 .doc 文本提取
# libreoffice：.doc/.ppt 转 .docx/.pptx 以提取内嵌图片
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-chi-sim libgomp1 antiword \
        libreoffice --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# 拷贝 venv 与应用代码
COPY --from=builder /venv /venv

WORKDIR /app

COPY app ./app
COPY rag ./rag
COPY config.py .

# 非 root 运行 + 数据卷就绪
RUN mkdir -p /app/data /app/storage && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

EXPOSE 8000

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
