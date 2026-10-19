FROM mcr.microsoft.com/playwright:v1.58.2-noble
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm install --prefix /opt/visual --ignore-scripts --no-audit --no-fund playwright@1.58.2
COPY browser-proof.cjs interactive-proof.cjs station-proof.cjs node-proof.cjs /opt/horizon/
WORKDIR /project
USER 65534:65534
CMD ["node", "/opt/horizon/browser-proof.cjs"]
