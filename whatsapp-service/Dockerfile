FROM node:20-alpine

WORKDIR /app

# Herramientas de build para dependencias nativas de Baileys
RUN apk add --no-cache python3 make g++ git

COPY package*.json ./
RUN npm install

# El directorio de sesiones se monta como volumen en runtime;
# lo creamos para que el proceso no falle si el volumen aún no está montado.
RUN mkdir -p /app/sessions

EXPOSE 3000

CMD ["node", "src/index.js"]
