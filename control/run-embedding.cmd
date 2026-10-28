@echo off
REM 由计划任务 DSH-Embedding 调用；也可以直接双击运行。
set HF_ENDPOINT=https://hf-mirror.com
set EMBED_MODEL=BAAI/bge-small-zh-v1.5
set TMP=D:\start\ds-harness\work\_tmp
set TEMP=D:\start\ds-harness\work\_tmp
"D:\cpython3.14\python.exe" "D:\start\ds-harness\evolution-stack\control\adapters\embeddings_server.py" --port 18080
