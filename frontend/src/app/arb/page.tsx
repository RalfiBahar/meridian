import { fetchArbViolations } from "@/lib/api";
import ArbMonitorClient from "@/components/ArbMonitorClient";

export const revalidate = 0;

export default async function ArbPage() {
  let data = null;
  try {
    data = await fetchArbViolations();
  } catch {
    // Initial data optional — client will receive via WS
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">Arb Monitor</span>
      </div>
      <ArbMonitorClient initialData={data} />
    </div>
  );
}
