import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";
import type {
  Alert,
  ApproachingVessel,
  Carrier,
  DataSource,
  FleetPositions,
  FleetSummary,
  Page,
  Port,
  PortCall,
  ReadinessResponse,
  TrackResponse,
  VesselDetail,
} from "./types";

export const queryKeys = {
  readiness: ["health", "ready"] as const,
  dataSources: ["data-sources"] as const,
  carriers: ["carriers"] as const,
  fleetPositions: ["fleet", "positions"] as const,
  fleetSummary: ["fleet", "summary"] as const,
  approaching: ["fleet", "approaching"] as const,
  vessel: (id: string) => ["vessels", id] as const,
  track: (id: string, hours: number) => ["vessels", id, "track", hours] as const,
  portCalls: (id: string) => ["vessels", id, "port-calls"] as const,
  ports: (bbox: string | null) => ["ports", bbox] as const,
  alerts: ["alerts", "open"] as const,
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

/** Map positions. Polls slowly as a fallback; live updates arrive over the WebSocket. */
export function useFleetPositions(pollMs: number) {
  return useQuery({
    queryKey: queryKeys.fleetPositions,
    queryFn: ({ signal }) => apiGet<FleetPositions>("/fleet/positions?limit=10000", { signal }),
    refetchInterval: pollMs,
  });
}

export function useFleetSummary() {
  return useQuery({
    queryKey: queryKeys.fleetSummary,
    queryFn: ({ signal }) => apiGet<FleetSummary>("/fleet/summary", { signal }),
    refetchInterval: 30_000,
  });
}

export function useApproaching() {
  return useQuery({
    queryKey: queryKeys.approaching,
    queryFn: ({ signal }) => apiGet<ApproachingVessel[]>("/fleet/approaching?within_nm=30&limit=8", { signal }),
    refetchInterval: 60_000,
  });
}

export function useVessel(id: string | null) {
  return useQuery({
    queryKey: queryKeys.vessel(id ?? ""),
    queryFn: ({ signal }) => apiGet<VesselDetail>(`/vessels/${id}`, { signal }),
    enabled: !!id,
    refetchInterval: 30_000,
  });
}

export function useVesselTrack(id: string | null, hours: number) {
  return useQuery({
    queryKey: queryKeys.track(id ?? "", hours),
    queryFn: ({ signal }) => {
      const start = new Date(Date.now() - hours * 3600_000).toISOString();
      return apiGet<TrackResponse>(`/vessels/${id}/track?start=${encodeURIComponent(start)}`, { signal });
    },
    enabled: !!id,
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });
}

export function useVesselPortCalls(id: string) {
  return useQuery({
    queryKey: queryKeys.portCalls(id),
    queryFn: ({ signal }) => apiGet<Page<PortCall>>(`/vessels/${id}/port-calls?limit=50`, { signal }),
    refetchInterval: 60_000,
  });
}

export function usePorts(bbox: string | null) {
  return useQuery({
    queryKey: queryKeys.ports(bbox),
    queryFn: ({ signal }) => apiGet<Port[]>(`/ports?bbox=${bbox}&limit=1500`, { signal }),
    enabled: !!bbox,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  });
}

export function useAlerts() {
  return useQuery({
    queryKey: queryKeys.alerts,
    queryFn: ({ signal }) => apiGet<Page<Alert>>("/alerts?status=open&limit=10", { signal }),
    refetchInterval: 30_000,
  });
}
