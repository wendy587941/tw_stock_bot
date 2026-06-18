# AWS Lambda Python base image（ARM64 由 build 時 --platform 指定）
FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# Lambda 容器進入點：<檔名>.<函式名>
CMD ["app.handler"]
