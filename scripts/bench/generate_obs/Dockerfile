# generate 行为观测实验的容器。
#
# 只装依赖，不装 xskill 本体：仓库源码在跑的时候挂进 /repo，靠
# PYTHONPATH=/repo/src 生效。这样改一行提示词或埋点立刻就能重跑，不用
# 每次重建镜像——用户接下来正是要手动微调。
#
# 基础镜像走 daocloud 镜像站：本机拉 registry-1.docker.io 会被 TLS 拦
# （证书是 *.dev.frame.io），Docker Hub 直连不可用。
FROM docker.m.daocloud.io/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 只把依赖清单拷进来，装完这层就能被缓存复用。
COPY pyproject.toml /build/pyproject.toml

RUN pip install --index-url https://mirrors.aliyun.com/pypi/simple/ \
        pyyaml>=6.0 \
        numpy>=1.24 \
        "openai>=1.0" \
        agno \
        tqdm \
        httpx \
        fastapi \
        python-multipart \
        uvicorn \
        sse-starlette \
        "packaging>=23" \
        "rank-bm25>=0.2" \
        "detect-secrets>=1.5" \
        "dulwich>=0.21" \
    && pip install --index-url https://mirrors.aliyun.com/pypi/simple/ \
        "opentelemetry-api>=1.20" \
        "opentelemetry-sdk>=1.20" \
        "opentelemetry-exporter-otlp-proto-http>=1.20"

# 实验产物与 mock home 都在挂载卷里，镜像本身不留数据。
ENV PYTHONPATH=/repo/src
WORKDIR /repo/scripts/bench/generate_obs

ENTRYPOINT ["python"]
CMD ["run_generate_job.py", "--help"]
