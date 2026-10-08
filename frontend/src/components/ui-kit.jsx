import React from 'react';
import {
  LayoutDashboard, Search, Map as MapIcon, Grid3x3, Users, FileSignature, UserCircle, Wallet, Inbox, BarChart3, Server, Cloud, Settings,
  HardDrive, Database, X, Menu, Bell, ExternalLink, LogOut, TrendingUp, AlertTriangle, ArrowRight, FileSpreadsheet, Plus, Pencil, Trash2,
  FileText, Check, Lock, ZoomIn, ZoomOut, Maximize, FileDown, Save, UserPlus, FilePlus, Hammer, ShieldCheck, Upload, ShieldAlert, KeyRound,
  Building2, Filter, ChevronLeft, ChevronRight, Phone, Paperclip, Camera, Download, RefreshCw,
} from 'lucide-react';
import { STATUS_META } from '../mock';
import { useApp } from '../store';

const ICONS = {
  'layout-dashboard': LayoutDashboard, search: Search, map: MapIcon, 'grid-3x3': Grid3x3, users: Users, 'file-signature': FileSignature,
  'user-circle': UserCircle, wallet: Wallet, inbox: Inbox, 'bar-chart-3': BarChart3, server: Server, cloud: Cloud, settings: Settings,
  'hard-drive': HardDrive, database: Database, x: X, menu: Menu, bell: Bell, 'external-link': ExternalLink, 'log-out': LogOut,
  'trending-up': TrendingUp, 'alert-triangle': AlertTriangle, 'arrow-right': ArrowRight, 'file-spreadsheet': FileSpreadsheet, plus: Plus,
  pencil: Pencil, 'trash-2': Trash2, 'file-text': FileText, check: Check, lock: Lock, 'zoom-in': ZoomIn, 'zoom-out': ZoomOut, maximize: Maximize,
  'file-down': FileDown, save: Save, 'user-plus': UserPlus, 'file-plus': FilePlus, hammer: Hammer, 'shield-check': ShieldCheck, upload: Upload,
  'shield-alert': ShieldAlert, 'key-round': KeyRound, 'building-2': Building2, filter: Filter, 'chevron-left': ChevronLeft, 'chevron-right': ChevronRight,
  phone: Phone, paperclip: Paperclip, camera: Camera, download: Download, 'refresh-cw': RefreshCw,
};

export const Icon = ({ name, className = '' }) => { const C = ICONS[name]; return C ? <C className={`lc ${className}`} /> : null; };

export const lei = (n) => `${Number(n || 0).toLocaleString('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} lei`;

export function StatusBadge({ status }) {
  return <span className={`badge status-${status}`}><span className="dot" />{STATUS_META[status]?.label || status}</span>;
}

const CS = { nou: ['cs-nou', 'Nou'], validat: ['cs-validat', 'Validat'], respins: ['cs-respins', 'Respins'], completari: ['cs-nou', 'Completări'] };
export function CerereStatus({ status }) {
  const [cls, label] = CS[status] || CS.nou;
  return <span className={`cerere-badge ${cls}`}>{label}</span>;
}

export function AttBtn({ etype, eid, label = 'Fișiere' }) {
  const { db, openAtt } = useApp();
  const n = db.attachments.filter((x) => x.etype === etype && String(x.eid) === String(eid)).length;
  return (
    <button type="button" className="att-btn" onClick={(e) => { e.stopPropagation(); openAtt(etype, eid); }} data-testid={`att-${etype}-${eid}`}>
      <Icon name="paperclip" /><span>{label}{n ? ` (${n})` : ''}</span>
    </button>
  );
}

export function PageHead({ title, sub, children }) {
  return <div className="page-head"><div><h1 className="page-title">{title}</h1>{sub && <p className="page-sub">{sub}</p>}</div>{children}</div>;
}

export function Modal({ open, title, onClose, children, testid }) {
  return (
    <div className={`modal-overlay ${open ? 'show' : ''}`} onClick={onClose}>
      <div className="modal" data-testid={testid} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><h3>{title}</h3><button type="button" onClick={onClose}><Icon name="x" /></button></div>
        {open && children}
      </div>
    </div>
  );
}

export const formObj = (e) => { e.preventDefault(); return Object.fromEntries(new FormData(e.target).entries()); };
