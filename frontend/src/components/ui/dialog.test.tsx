import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Menu } from './Menu';
import { Modal } from './Modal';
import { Select } from './Select';
import { SourceBadge } from './SourceBadge';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const esc = (target: EventTarget) =>
  act(() => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  });

function SelectInModal({ onClose }: { onClose: () => void }) {
  const [v, setV] = useState<string>('a');
  return (
    <Modal open title="Sửa kết nối" onClose={onClose}>
      <Select
        id="model"
        label="Model"
        value={v}
        onChange={setV}
        options={[
          { value: 'a', label: 'A' },
          { value: 'b', label: 'B' },
        ]}
      />
    </Modal>
  );
}

describe('Esc trong dialog', () => {
  it('Esc khi không có popup → đóng modal', () => {
    const onClose = vi.fn();
    act(() => root.render(<SelectInModal onClose={onClose} />));
    esc(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Esc trong Select đang mở bên trong Modal → chỉ đóng danh sách, modal vẫn mở', () => {
    const onClose = vi.fn();
    act(() => root.render(<SelectInModal onClose={onClose} />));
    const trigger = document.getElementById('model') as HTMLButtonElement;
    act(() => trigger.click());
    const list = document.querySelector('[role="listbox"]');
    expect(list).not.toBeNull();
    esc(list!);
    expect(document.querySelector('[role="listbox"]')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it('Esc trong Menu bên trong Modal → chỉ đóng menu', () => {
    const onClose = vi.fn();
    act(() =>
      root.render(
        <Modal open title="X" onClose={onClose}>
          <Menu
            label="Hành động"
            items={[{ key: 'a', label: 'Một', onSelect: () => {} }]}
            trigger={(p) => (
              <button {...p} type="button" id="menu-btn">
                Mở
              </button>
            )}
          />
        </Modal>,
      ),
    );
    act(() => (document.getElementById('menu-btn') as HTMLButtonElement).click());
    const menu = document.querySelector('[role="menu"]');
    expect(menu).not.toBeNull();
    esc(menu!);
    expect(document.querySelector('[role="menu"]')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('Esc trong popover SourceBadge bên trong Modal → chỉ đóng popover', () => {
    const onClose = vi.fn();
    act(() =>
      root.render(
        <Modal open title="X" onClose={onClose}>
          <SourceBadge source="preset" editing="clip" values={{ preset: '720p' }} />
        </Modal>,
      ),
    );
    const btn = document.querySelector('button[aria-expanded]') as HTMLButtonElement;
    act(() => btn.click());
    const pop = document.querySelector('[aria-label="Giá trị theo từng cấp"]');
    expect(pop).not.toBeNull();
    esc(pop!);
    expect(document.querySelector('[aria-label="Giá trị theo từng cấp"]')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('hai dialog chồng nhau → Esc chỉ đóng dialog trên cùng', () => {
    const outer = vi.fn();
    const inner = vi.fn();
    function Stacked() {
      const [innerOpen, setInnerOpen] = useState(true);
      return (
        <Modal open title="Ngoài" onClose={outer}>
          <Modal
            open={innerOpen}
            title="Trong"
            onClose={() => {
              inner();
              setInnerOpen(false);
            }}
          />
        </Modal>
      );
    }
    act(() => root.render(<Stacked />));
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2);
    esc(document.activeElement ?? document.body);
    expect(inner).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    // Esc tiếp theo mới tới dialog ngoài
    esc(document.body);
    expect(outer).toHaveBeenCalledTimes(1);
  });

  it('modal không đóng được (phiên hết hạn) không bị Esc đóng', () => {
    act(() => root.render(<Modal open title="Phiên đã hết hạn" />));
    esc(document.body);
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });
});
