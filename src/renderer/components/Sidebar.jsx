import React from 'react';
import { LayoutDashboard, SlidersHorizontal, Video } from 'lucide-react';

const tabs = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'calibration', label: 'Calibrate', icon: SlidersHorizontal },
  { id: 'live', label: 'Live View', icon: Video },
];

export default function Sidebar({ activeTab, onTabChange, isTracking }) {
  return (
    <aside className="w-16 bg-panel border-r border-panel-border flex flex-col items-center py-4 gap-2 shrink-0">
      {tabs.map(({ id, label, icon: Icon }) => {
        const active = activeTab === id;
        return (
          <button
            key={id}
            onClick={() => onTabChange(id)}
            title={label}
            className={`
              w-11 h-11 rounded-lg flex items-center justify-center transition-all duration-200
              ${active
                ? 'bg-neon/10 text-neon shadow-neon'
                : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'}
            `}
          >
            <Icon size={20} />
          </button>
        );
      })}

      <div className="mt-auto">
        <div
          className={`w-3 h-3 rounded-full transition-all duration-500 ${
            isTracking
              ? 'bg-neon shadow-neon animate-pulse-neon'
              : 'bg-gray-600'
          }`}
          title={isTracking ? 'Tracking Active' : 'Tracking Off'}
        />
      </div>
    </aside>
  );
}
