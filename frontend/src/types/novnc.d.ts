// The parts of noVNC's RFB (@novnc/novnc 1.7.0, core/rfb.js) that LiveView uses.
declare module '@novnc/novnc' {
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, url: string, options?: { wsProtocols?: string[]; shared?: boolean })
    scaleViewport: boolean
    resizeSession: boolean
    focusOnClick: boolean
    viewOnly: boolean
    disconnect(): void
    addEventListener(type: 'disconnect', listener: (e: CustomEvent<{ clean: boolean }>) => void): void
    addEventListener(type: string, listener: (e: CustomEvent) => void): void
  }
}
