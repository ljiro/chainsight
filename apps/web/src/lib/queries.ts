import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";
import type { Carrier, DataSource, Page, ReadinessResponse } from "./types";

export const queryKeys = {
  readiness: ["health", "ready"] as const,
  dataSources: ["data-sources"] as const,
  carriers: ["carriers"] as const,
};

export function useReadiness() {
  return useQuery({
    queryKey: queryKeys.readiness,
    queryFn: ({ signal }) =>
      apiGet<ReadinessResponse>("/health/ready", { signal, acceptStatuses: [503] }),
    refetchInterval: 15_000,
  });
}

export function useDataSources() {
  return useQuery({
    queryKey: queryKeys.dataSources,
    queryFn: ({ signal }) => apiGet<Page<DataSource>>("/data-sources?limit=200", { signal }),
    refetchInterval: 60_000,
  });
}

export function useCarriers() {
  return useQuery({
    queryKey: queryKeys.carriers,
    queryFn: ({ signal }) => apiGet<Page<Carrier>>("/carriers?limit=200", { signal }),
  });
}
