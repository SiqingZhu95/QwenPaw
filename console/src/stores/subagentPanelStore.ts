import { create } from "zustand";

export type SubagentExecutionStatus = "running" | "completed" | "failed";

export interface SubagentTab {
  /** Stable tool-call id. The session id is not available yet in foreground mode. */
  id: string;
  agentId?: string;
  sessionId?: string;
  taskId?: string;
  task: string;
  background: boolean;
  status: SubagentExecutionStatus;
  error?: string;
}

export interface SubagentTabUpdate {
  id: string;
  agentId?: string;
  sessionId?: string;
  taskId?: string;
  task?: string;
  background?: boolean;
  status?: SubagentExecutionStatus;
  error?: string;
}

interface SubagentPanelState {
  open: boolean;
  activeTabId: string | null;
  tabs: SubagentTab[];
  openTab: (tab: SubagentTab) => void;
  syncTab: (update: SubagentTabUpdate) => void;
  setActiveTab: (id: string) => void;
  closeTab: (id: string) => void;
  closePanel: () => void;
  reset: () => void;
}

function mergeTab(tab: SubagentTab, update: SubagentTabUpdate): SubagentTab {
  return {
    ...tab,
    ...(update.agentId ? { agentId: update.agentId } : {}),
    ...(update.sessionId ? { sessionId: update.sessionId } : {}),
    ...(update.taskId ? { taskId: update.taskId } : {}),
    ...(update.task !== undefined ? { task: update.task } : {}),
    ...(update.background !== undefined
      ? { background: update.background }
      : {}),
    ...(update.status ? { status: update.status } : {}),
    ...(update.error !== undefined ? { error: update.error } : {}),
  };
}

export const useSubagentPanelStore = create<SubagentPanelState>((set) => ({
  open: false,
  activeTabId: null,
  tabs: [],

  openTab: (tab) =>
    set((state) => {
      const existingIndex = state.tabs.findIndex(
        (item) =>
          item.id === tab.id ||
          (!!tab.sessionId && item.sessionId === tab.sessionId),
      );
      const tabs = [...state.tabs];
      if (existingIndex >= 0) {
        tabs[existingIndex] = mergeTab(tabs[existingIndex], tab);
      } else {
        tabs.push(tab);
      }
      const active = existingIndex >= 0 ? tabs[existingIndex] : tab;
      return { tabs, open: true, activeTabId: active.id };
    }),

  // Tool cards keep rendering while their result streams in. Only update a
  // tab that the user has explicitly opened; never pop open tabs by itself.
  syncTab: (update) =>
    set((state) => {
      const index = state.tabs.findIndex((tab) => tab.id === update.id);
      if (index < 0) return state;
      const tabs = [...state.tabs];
      tabs[index] = mergeTab(tabs[index], update);
      return { tabs };
    }),

  setActiveTab: (id) => set({ activeTabId: id, open: true }),

  closeTab: (id) =>
    set((state) => {
      const index = state.tabs.findIndex((tab) => tab.id === id);
      if (index < 0) return state;
      const tabs = state.tabs.filter((tab) => tab.id !== id);
      if (tabs.length === 0) {
        return { tabs, activeTabId: null, open: false };
      }
      const activeTabId =
        state.activeTabId === id
          ? tabs[Math.min(index, tabs.length - 1)].id
          : state.activeTabId;
      return { tabs, activeTabId };
    }),

  closePanel: () => set({ open: false }),
  reset: () => set({ open: false, activeTabId: null, tabs: [] }),
}));
