import { Suspense } from "react";

import { RemediationsImpactClientPage } from "./RemediationsImpactClientPage";

const loadingFallback = (
  <main className="finops-shell relative overflow-hidden">
    <div className="finops-orb finops-orb--one" />
    <div className="finops-orb finops-orb--two" />
    <div className="finops-orb finops-orb--three" />
    <div className="relative z-10 mx-auto min-h-screen w-full max-w-7xl px-6 py-6">
      <section className="finops-panel rounded-2xl p-6 text-sm text-slate-700">
        Loading realized savings...
      </section>
    </div>
  </main>
);

export default function RemediationsPage() {
  return (
    <Suspense fallback={loadingFallback}>
      <RemediationsImpactClientPage />
    </Suspense>
  );
}
