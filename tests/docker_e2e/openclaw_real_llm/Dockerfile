# 真 openclaw + 真 LLM 的 e2e 镜像
# 与共享 rig (patentdagger-api 7GB base) 分开，因为这里要装 node 24 + openclaw npm
# 包 (~815MB)。只有手动跑 make e2e-openclaw-real 时才会 build。
FROM node:24-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates jq python3.11 python3-pip python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

# pypi 阿里云内网镜像，跟其他 e2e rig 一致
ENV PIP_INDEX_URL=http://mirrors.cloud.aliyuncs.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.cloud.aliyuncs.com \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=3 \
    PIP_BREAK_SYSTEM_PACKAGES=1

# 装真 openclaw (固定版本)
RUN npm install -g openclaw@2026.5.7 \
    && rm -rf /root/.npm

# 装 xskill (本地 wheel 注入)
COPY dist /tmp/xskill_dist
RUN python3.11 -m pip install --no-cache-dir --pre /tmp/xskill_dist/*.whl httpx pyyaml fastapi 'uvicorn[standard]' \
    && rm -rf /tmp/xskill_dist

# 测试用户
RUN useradd -m -s /bin/bash xtest
WORKDIR /home/xtest
ENV HOME=/home/xtest
ENV TESTHOME=/home/xtest

# 拷 entrypoint + openclaw 最小配置
COPY tests/docker_e2e/openclaw_real_llm/run.sh /run.sh
COPY tests/docker_e2e/openclaw_real_llm/openclaw.json /tmp/openclaw.json
COPY tests/docker_e2e/openclaw_real_llm/xskill_config.yaml /tmp/xskill_config.yaml
COPY tests/docker_e2e/openclaw_real_llm/demo_skill /tmp/demo_skill
RUN chmod +x /run.sh && chown -R xtest:xtest /home/xtest

USER xtest
ENTRYPOINT ["/run.sh"]
