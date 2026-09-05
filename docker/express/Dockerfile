FROM node:22-bookworm-slim

ENV NODE_ENV=development
ENV NPM_CONFIG_UPDATE_NOTIFIER=false

RUN useradd --create-home --shell /bin/bash runner

WORKDIR /workspace

COPY package.json package-lock.json ./

RUN npm ci --omit=optional && \
    npm cache clean --force && \
    chown -R runner:runner /workspace

USER runner

CMD ["tail", "-f", "/dev/null"]