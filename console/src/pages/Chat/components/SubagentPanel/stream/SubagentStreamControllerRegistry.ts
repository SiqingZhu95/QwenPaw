import { SubagentStreamController } from "./SubagentStreamController";
import type { SubagentStreamOwner } from "./types";

interface ControllerRegistration {
  key: string;
  tabId: string;
  parentToolCallId: string;
  owner: SubagentStreamOwner;
  waitForBinding?: boolean;
}

class SubagentStreamControllerRegistry {
  private readonly controllers = new Map<string, SubagentStreamController>();

  getOrCreate(registration: ControllerRegistration): SubagentStreamController {
    const existing = this.controllers.get(registration.key);
    if (existing) return existing;
    const controller = new SubagentStreamController(registration);
    this.controllers.set(registration.key, controller);
    return controller;
  }

  prefetch(registration: ControllerRegistration): void {
    void this.getOrCreate(registration).prefetch();
  }

  activate(key: string): void {
    this.controllers.get(key)?.activate();
  }

  deactivate(key: string): void {
    this.controllers.get(key)?.deactivate();
  }

  dispose(key: string): void {
    this.controllers.get(key)?.dispose();
    this.controllers.delete(key);
  }

  reset(): void {
    for (const controller of this.controllers.values()) controller.dispose();
    this.controllers.clear();
  }
}

export const subagentStreamControllerRegistry =
  new SubagentStreamControllerRegistry();
