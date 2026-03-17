import type { NextConfig } from "next";

const buildDistDir = process.env.BUILD_DIST_DIR?.trim();

const nextConfig: NextConfig = {
  /* config options here */
  reactCompiler: true,
  ...(buildDistDir ? { distDir: buildDistDir } : {}),
};

export default nextConfig;
