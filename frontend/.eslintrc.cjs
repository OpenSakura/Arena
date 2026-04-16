module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: {
      jsx: true,
    },
  },
  plugins: ["@typescript-eslint"],
  extends: ["eslint:recommended", "plugin:@typescript-eslint/recommended"],
  ignorePatterns: [
    "dist/",
    "node_modules/",
    ".next/",
    "debug.js",
    "out.log",
    "output.txt",
    "e2e/",
    "playwright-report/",
    "test-results/",
    "src/app/**",
    "src/components/**",
    "src/hooks/**",
    "src/lib/**",
    "src/middleware.ts",
    "src/types/**",
  ],
  overrides: [
    {
      files: ["tailwind.config.ts"],
      rules: {
        "@typescript-eslint/no-require-imports": "off",
      },
    },
  ],
};
