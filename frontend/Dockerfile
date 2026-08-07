FROM node:22-alpine AS build
ENV NPM_CONFIG_FETCH_RETRIES=10 \
    NPM_CONFIG_FETCH_RETRY_MINTIMEOUT=1000 \
    NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=30000 \
    NPM_CONFIG_FETCH_TIMEOUT=120000
WORKDIR /app
COPY package*.json ./
RUN npm ci --include=optional \
    && if ! node -e "require('lightningcss')"; then \
        package="lightningcss-linux-$(node -p 'process.arch')-musl"; \
        version="$(node -p "require('./node_modules/lightningcss/package.json').version")"; \
        npm install --no-save --package-lock=false "${package}@${version}"; \
    fi \
    && node -e "require('lightningcss')"
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
