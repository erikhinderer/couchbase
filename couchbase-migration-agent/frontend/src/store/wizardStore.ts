import { create } from "zustand";

export type WizardStrategy = "backup_restore" | "xdcr_live" | "hybrid";

export interface ClusterFormState {
  label: string;
  connection_string: string;
  username: string;
  password: string;
  is_capella: boolean;
  capella_cluster_id?: string;
  capella_project_id?: string;
  use_tls: boolean;
  use_external_network: boolean;
}

const emptyCluster = (label: string, isCapella = false): ClusterFormState => ({
  label,
  connection_string: isCapella ? "couchbases://" : "couchbase://",
  username: "",
  password: "",
  is_capella: isCapella,
  use_tls: isCapella,
  use_external_network: false,
});

interface WizardState {
  step: number;
  migrationName: string;
  source: ClusterFormState;
  destination: ClusterFormState;
  strategy: WizardStrategy;
  selectedBuckets: string[];
  migrationId?: string;
  setStep: (n: number) => void;
  setMigrationName: (v: string) => void;
  updateSource: (patch: Partial<ClusterFormState>) => void;
  updateDestination: (patch: Partial<ClusterFormState>) => void;
  setStrategy: (s: WizardState["strategy"]) => void;
  setSelectedBuckets: (b: string[]) => void;
  setMigrationId: (id: string) => void;
  reset: () => void;
}

export const useWizardStore = create<WizardState>((set) => ({
  step: 0,
  migrationName: "",
  source: emptyCluster("Source Cluster"),
  destination: emptyCluster("Capella Destination", true),
  strategy: "backup_restore",
  selectedBuckets: [],
  setStep: (n) => set({ step: n }),
  setMigrationName: (v) => set({ migrationName: v }),
  updateSource: (patch) => set((s) => ({ source: { ...s.source, ...patch } })),
  updateDestination: (patch) => set((s) => ({ destination: { ...s.destination, ...patch } })),
  setStrategy: (strategy) => set({ strategy }),
  setSelectedBuckets: (selectedBuckets) => set({ selectedBuckets }),
  setMigrationId: (migrationId) => set({ migrationId }),
  reset: () =>
    set({
      step: 0,
      migrationName: "",
      source: emptyCluster("Source Cluster"),
      destination: emptyCluster("Capella Destination", true),
      strategy: "backup_restore",
      selectedBuckets: [],
      migrationId: undefined,
    }),
}));
