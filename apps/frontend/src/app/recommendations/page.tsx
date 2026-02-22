import { Suspense } from "react";

import { RecommendationsClientPage } from "./RecommendationsClientPage";

export default function RecommendationsPage() {
  return (
    <Suspense
      fallback={<main className="mx-auto min-h-screen w-full max-w-6xl px-6 py-8">Loading recommendations...</main>}
    >
      <RecommendationsClientPage />
    </Suspense>
  );
}
