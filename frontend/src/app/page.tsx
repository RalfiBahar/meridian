import { fetchMarkets } from "@/lib/api";
import HomeClient from "@/components/HomeClient";

export const revalidate = 30;

export default async function Home() {
  let stats: { markets?: number; healthy?: boolean } = { healthy: false };

  try {
    const data = await fetchMarkets({ status: "open", limit: 1, offset: 0 });
    stats = { healthy: true, markets: data.total };
  } catch {
    stats = { healthy: false };
  }

  return <HomeClient stats={stats} />;
}
