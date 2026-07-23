import { create } from "zustand";
import type { SubagentStreamRecord } from "./types";

interface SubagentStreamState {
  records: Record<string, SubagentStreamRecord>;
  ensure: (record: SubagentStreamRecord) => void;
  patch: (key: string, patch: Partial<SubagentStreamRecord>) => void;
  remove: (key: string) => void;
  reset: () => void;
}

export const useSubagentStreamStore = create<SubagentStreamState>((set) => ({
  records: {},
  ensure: (record) =>
    set((state) =>
      state.records[record.key]
        ? state
        : { records: { ...state.records, [record.key]: record } },
    ),
  patch: (key, patch) =>
    set((state) => {
      const current = state.records[key];
      if (!current) return state;
      return {
        records: {
          ...state.records,
          [key]: { ...current, ...patch, key: current.key },
        },
      };
    }),
  remove: (key) =>
    set((state) => {
      if (!state.records[key]) return state;
      const records = { ...state.records };
      delete records[key];
      return { records };
    }),
  reset: () => set({ records: {} }),
}));
