process.env.NODE_ENV = process.env.NODE_ENV || "development";
process.env.__NEXT_DISABLE_MEMORY_WATCHER = "1";

const { loadEnvConfig } = require("@next/env");
const { startServer } = require("next/dist/server/lib/start-server");

loadEnvConfig(process.cwd(), true);

startServer({
  dir: process.cwd(),
  port: 3000,
  hostname: "127.0.0.1",
  isDev: true,
  allowRetry: false,
}).catch((error) => {
  console.error(error);
  process.exit(1);
});
