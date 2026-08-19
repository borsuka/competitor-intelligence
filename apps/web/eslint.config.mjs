import { FlatCompat } from "@eslint/eslintrc";

// Flat config. `next lint` is deprecated and removed in Next 16, so this project runs
// the ESLint CLI directly; FlatCompat bridges eslint-config-next, which still ships the
// legacy format.
const compat = new FlatCompat({ baseDirectory: import.meta.dirname });

const config = [
  // .next-e2e is the end-to-end suite's build output; linting generated bundles
  // produces thousands of findings about code nobody wrote.
  { ignores: [".next/**", ".next-e2e/**", "node_modules/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
];

export default config;
