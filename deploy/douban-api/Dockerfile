FROM node:16-bullseye-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    bzip2 \
    fontconfig \
    fonts-noto-cjk \
    libfreetype6 \
    libfontconfig1 \
    libxrender1 \
    libxext6 \
    libx11-6 \
    && rm -rf /var/lib/apt/lists/*

# PhantomJS 2.1.1 tries to load OpenSSL modules that are not present in slim images.
# Point it at an empty config file so the Phantom process can start reliably.
RUN touch /tmp/openssl-empty.cnf

ARG PHANTOMJS_VERSION=2.1.1
RUN curl -fsSL "https://github.com/Medium/phantomjs/releases/download/v${PHANTOMJS_VERSION}/phantomjs-${PHANTOMJS_VERSION}-linux-x86_64.tar.bz2" -o /tmp/phantomjs.tar.bz2 \
    && tar -xjf /tmp/phantomjs.tar.bz2 -C /tmp \
    && mv "/tmp/phantomjs-${PHANTOMJS_VERSION}-linux-x86_64/bin/phantomjs" /usr/local/bin/phantomjs \
    && chmod +x /usr/local/bin/phantomjs \
    && rm -rf /tmp/phantomjs*

WORKDIR /app

COPY package*.json ./
RUN npm install --production

COPY . .

ENV NODE_ENV=production
ENV OPENSSL_CONF=/tmp/openssl-empty.cnf
EXPOSE 8085

CMD ["node", "app.js"]
