import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown } from 'lucide-react';

const commonKeys = [
  'Space', 'W', 'A', 'S', 'D', 'E', 'Q', 'R', 'F',
  'Up', 'Down', 'Left', 'Right',
  'Shift', 'Ctrl', 'Enter',
  '1', '2', '3', '4', '5',
];

export default function KeySelector({ currentKey, onChange }) {
  const [open, setOpen] = useState(false);
  const [listening, setListening] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false);
        setListening(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (!listening) return;
    const handleKey = (e) => {
      e.preventDefault();
      let key = e.key;
      if (key === ' ') key = 'Space';
      else if (key === 'ArrowUp') key = 'Up';
      else if (key === 'ArrowDown') key = 'Down';
      else if (key === 'ArrowLeft') key = 'Left';
      else if (key === 'ArrowRight') key = 'Right';
      else if (key === 'Control') key = 'Ctrl';
      else key = key.length === 1 ? key.toUpperCase() : key;
      onChange(key);
      setListening(false);
      setOpen(false);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [listening, onChange]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-panel border border-panel-border rounded-lg
                   hover:border-neon/30 transition-colors text-sm"
      >
        <kbd className="text-neon font-mono text-xs">{currentKey}</kbd>
        <ChevronDown size={12} className="text-gray-500" />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 w-48 bg-surface border border-panel-border rounded-lg shadow-xl z-50 p-2 animate-fade-in">
          <button
            onClick={() => { setListening(true); }}
            className={`w-full text-left text-xs px-3 py-2 rounded mb-1 transition-colors ${
              listening
                ? 'bg-neon/10 text-neon border border-neon/30'
                : 'text-gray-400 hover:bg-white/5 hover:text-white'
            }`}
          >
            {listening ? '⌨ Press any key...' : '⌨ Bind custom key'}
          </button>
          <div className="border-t border-panel-border my-1" />
          <div className="grid grid-cols-3 gap-1 max-h-40 overflow-y-auto">
            {commonKeys.map((k) => (
              <button
                key={k}
                onClick={() => { onChange(k); setOpen(false); }}
                className={`text-xs px-2 py-1.5 rounded transition-colors font-mono
                  ${k === currentKey
                    ? 'bg-neon/15 text-neon border border-neon/30'
                    : 'text-gray-400 hover:bg-white/5 hover:text-white'
                  }`}
              >
                {k}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
