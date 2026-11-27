import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "./api";
import type {
  Alert,
  ApproachingVessel,
  Carrier,
  CarrierPerformance,
  ImportReport,
  ShipmentDetail,
  ShipmentsSummary,
  ShipmentSummary,
  DataSource,
  FleetPositions,
  FleetSummary,
  Page,
  Port,
  PortCall,
  ReadinessResponse,
  TrackResponse,
  VesselDetail,
  CongestionPort,
  DelayPrediction,
  EtaPrediction,
  ModelVersion,
  MultipleTesting,
  PortCongestion,
  PredictionsSummary,
  VesselAnomaly,
  Analysis,
  ExperimentDetail,
  Experiment,
  FeatureSeries,
  IndicatorCatalog,
  MarketSeries,
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
  shipments: (params: string) => ["shipments", "list", params] as const,
  shipment: (id: string) => ["shipments", "detail", id] as const,
  shipmentsSummary: ["shipments", "summary"] as const,
  carrierPerformance: ["carriers", "performance"] as const,
  models: ["predictions", "models"] as const,
  predictionsSummary: ["predictions", "summary"] as const,
  etaPredictions: ["predictions", "eta"] as const,
  vesselEta: (id: string) => ["predictions", "eta", id] as const,
  delayPredictions: ["predictions", "delay"] as const,
  shipmentDelay: (id: string) => ["predictions", "delay", id] as const,
  congestion: ["predictions", "congestion"] as const,
  portCongestion: (id: string) => ["predictions", "congestion", id] as const,
  anomalies: (vesselId: string | null, status: string) => ["predictions", "anomalies", vesselId, status] as const,
  indicators: ["research", "indicators"] as const,
  feature: (key: string) => ["research", "feature", key] as const,
  market: (key: string) => ["research", "market", key] as const,
  analysis: (feature: string, asset: string, lag: number) => ["research", "analysis", feature, asset, lag] as const,
  experiments: ["research", "experiments"] as const,
  multipleTesting: ["research", "experiments", "multiple-testing"] as const,
  experiment: (id: string) => ["research", "experiment", id] as const,
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

export function useShipments(params: URLSearchParams) {
  const qs = params.toString();
  return useQuery({
    queryKey: queryKeys.shipments(qs),
    queryFn: ({ signal }) => apiGet<Page<ShipmentSummary>>(`/shipments?${qs}`, { signal }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
}

export function useShipment(id: string) {
  return useQuery({
    queryKey: queryKeys.shipment(id),
    queryFn: ({ signal }) => apiGet<ShipmentDetail>(`/shipments/${id}`, { signal }),
    refetchInterval: 60_000,
  });
}

export function useShipmentsSummary() {
  return useQuery({
    queryKey: queryKeys.shipmentsSummary,
    queryFn: ({ signal }) => apiGet<ShipmentsSummary>("/shipments/summary", { signal }),
    refetchInterval: 60_000,
  });
}

export function useCarrierPerformance() {
  return useQuery({
    queryKey: queryKeys.carrierPerformance,
    queryFn: ({ signal }) => apiGet<CarrierPerformance[]>("/carriers/performance", { signal }),
    refetchInterval: 60_000,
  });
}

function useInvalidateShipments() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["shipments"] });
    void qc.invalidateQueries({ queryKey: queryKeys.carrierPerformance });
  };
}

export function useTrackShipment() {
  const invalidate = useInvalidateShipments();
  return useMutation({
    mutationFn: (body: { carrier: string; reference: string; reference_type?: string }) =>
      apiPost<ShipmentDetail>("/shipments/track", body),
    onSuccess: invalidate,
  });
}

export function useRefreshShipment(id: string) {
  const invalidate = useInvalidateShipments();
  return useMutation({
    mutationFn: () => apiPost<ShipmentDetail>(`/shipments/${id}/refresh`, {}),
    onSuccess: invalidate,
  });
}

export function useImportShipments() {
  const invalidate = useInvalidateShipments();
  return useMutation({
    // 422 still carries the per-row report, so it is returned rather than thrown.
    mutationFn: (csv: string) =>
      apiPost<ImportReport>("/shipments/import", csv, { contentType: "text/csv", acceptStatuses: [422] }),
    onSuccess: (report) => {
      if (report.ok) invalidate();
    },
  });
}

// ---- Predictive intelligence ---------------------------------------------------------------

export function useModels() {
  return useQuery({
    queryKey: queryKeys.models,
    queryFn: ({ signal }) => apiGet<ModelVersion[]>("/models", { signal }),
    refetchInterval: 5 * 60_000,
  });
}

export function usePredictionsSummary() {
  return useQuery({
    queryKey: queryKeys.predictionsSummary,
    queryFn: ({ signal }) => apiGet<PredictionsSummary>("/predictions/summary", { signal }),
    refetchInterval: 60_000,
  });
}

export function useEtaPredictions() {
  return useQuery({
    queryKey: queryKeys.etaPredictions,
    queryFn: ({ signal }) => apiGet<EtaPrediction[]>("/predictions/eta?limit=50", { signal }),
    refetchInterval: 60_000,
  });
}

export function useVesselEta(id: string) {
  return useQuery({
    queryKey: queryKeys.vesselEta(id),
    queryFn: ({ signal }) => apiGet<EtaPrediction[]>(`/vessels/${id}/eta`, { signal }),
    refetchInterval: 60_000,
  });
}

export function useDelayPredictions() {
  return useQuery({
    queryKey: queryKeys.delayPredictions,
    queryFn: ({ signal }) => apiGet<DelayPrediction[]>("/predictions/delay?limit=50", { signal }),
    refetchInterval: 60_000,
  });
}

export function useShipmentDelayRisk(id: string) {
  return useQuery({
    queryKey: queryKeys.shipmentDelay(id),
    queryFn: ({ signal }) => apiGet<DelayPrediction[]>(`/shipments/${id}/delay-risk`, { signal }),
    refetchInterval: 60_000,
  });
}

export function useCongestion() {
  return useQuery({
    queryKey: queryKeys.congestion,
    queryFn: ({ signal }) => apiGet<CongestionPort[]>("/predictions/congestion?limit=20", { signal }),
    refetchInterval: 5 * 60_000,
  });
}

export function usePortCongestion(id: string | null) {
  return useQuery({
    queryKey: queryKeys.portCongestion(id ?? ""),
    queryFn: ({ signal }) => apiGet<PortCongestion>(`/ports/${id}/congestion`, { signal }),
    enabled: !!id,
    placeholderData: keepPreviousData,
    refetchInterval: 5 * 60_000,
  });
}

export function useAnomalies(vesselId: string | null, status: "open" | "resolved" | "all" = "open") {
  return useQuery({
    queryKey: queryKeys.anomalies(vesselId, status),
    queryFn: ({ signal }) => {
      const qs = new URLSearchParams({ status, limit: "50" });
      if (vesselId) qs.set("vessel_id", vesselId);
      return apiGet<VesselAnomaly[]>(`/anomalies?${qs}`, { signal });
    },
    refetchInterval: 60_000,
  });
}

// ---- Quantitative research -----------------------------------------------------------------

export function useResearchIndicators() {
  return useQuery({
    queryKey: queryKeys.indicators,
    queryFn: ({ signal }) => apiGet<IndicatorCatalog>("/research/indicators", { signal }),
    staleTime: 10 * 60_000,
  });
}

export function useFeatureSeries(key: string | null) {
  return useQuery({
    queryKey: queryKeys.feature(key ?? ""),
    queryFn: ({ signal }) => apiGet<FeatureSeries>(`/research/features?key=${encodeURIComponent(key!)}`, { signal }),
    enabled: !!key,
    placeholderData: keepPreviousData,
    staleTime: 10 * 60_000,
    retry: false,
  });
}

export function useMarketSeries(key: string | null) {
  return useQuery({
    queryKey: queryKeys.market(key ?? ""),
    queryFn: ({ signal }) => apiGet<MarketSeries>(`/research/market?key=${encodeURIComponent(key!)}`, { signal }),
    enabled: !!key,
    placeholderData: keepPreviousData,
    staleTime: 10 * 60_000,
  });
}

/** Analyses are deterministic for given inputs, so they are cached like reads. */
export function useAnalysis(feature: string | null, asset: string | null, executionLagDays = 0) {
  return useQuery({
    queryKey: queryKeys.analysis(feature ?? "", asset ?? "", executionLagDays),
    queryFn: () =>
      apiPost<Analysis>("/research/analysis", { feature, asset, execution_lag_days: executionLagDays }),
    enabled: !!feature && !!asset,
    placeholderData: keepPreviousData,
    staleTime: 10 * 60_000,
    retry: false,
  });
}

export function useExperiments() {
  return useQuery({
    queryKey: queryKeys.experiments,
    queryFn: ({ signal }) => apiGet<Experiment[]>("/research/experiments", { signal }),
  });
}

export function useMultipleTesting() {
  return useQuery({
    queryKey: queryKeys.multipleTesting,
    queryFn: ({ signal }) => apiGet<MultipleTesting>("/research/experiments/multiple-testing", { signal }),
  });
}

export function useExperiment(id: string | null) {
  return useQuery({
    queryKey: queryKeys.experiment(id ?? ""),
    queryFn: ({ signal }) => apiGet<ExperimentDetail>(`/research/experiments/${id}`, { signal }),
    enabled: !!id,
  });
}

function useInvalidateResearch() {
  const qc = useQueryClient();
  return (detail?: ExperimentDetail) => {
    void qc.invalidateQueries({ queryKey: queryKeys.experiments });
    if (detail) qc.setQueryData(queryKeys.experiment(detail.id), detail);
  };
}

export function useCreateExperiment() {
  const invalidate = useInvalidateResearch();
  return useMutation({
    mutationFn: (body: { name: string; hypothesis: string; config: Record<string, unknown> }) =>
      apiPost<ExperimentDetail>("/research/experiments", body),
    onSuccess: invalidate,
  });
}

export function useRerunExperiment(id: string) {
  const invalidate = useInvalidateResearch();
  return useMutation({
    mutationFn: () => apiPost<ExperimentDetail>(`/research/experiments/${id}/runs`, {}),
    onSuccess: invalidate,
  });
}
