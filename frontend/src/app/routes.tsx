import { lazy } from 'react';
import { Route, Routes } from 'react-router';
import { PlaceholderPage } from '../components/PlaceholderPage';
import { Layout } from './Layout';

// Route-level code splitting keeps the initial chunk small.
const HomePage = lazy(() =>
  import('../features/home/HomePage').then((m) => ({ default: m.HomePage })),
);
const EditorPage = lazy(() => import('../features/editor/EditorPage'));
const ResultPage = lazy(() => import('../features/result/ResultPage'));
const HelpPage = lazy(() => import('../features/help/HelpPage'));
const PrintersPage = lazy(() => import('../features/printers/PrintersPage'));
const PrinterFormPage = lazy(() => import('../features/printers/PrinterFormPage'));
const CalibrationPage = lazy(() => import('../features/printers/CalibrationPage'));
const PresetsPage = lazy(() => import('../features/presets/PresetsPage'));
const HistoryPage = lazy(() => import('../features/history/HistoryPage'));
const LoginPage = lazy(() => import('../features/login/LoginPage'));
const SettingsPage = lazy(() => import('../features/settings/SettingsPage'));

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="documents/:id" element={<EditorPage />} />
        <Route path="jobs/:id" element={<ResultPage />} />
        <Route path="printers" element={<PrintersPage />} />
        <Route path="printers/new" element={<PrinterFormPage />} />
        <Route path="printers/:id" element={<PrinterFormPage />} />
        <Route path="printers/:id/calibrate" element={<CalibrationPage />} />
        <Route path="presets" element={<PresetsPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="help" element={<HelpPage />} />
        <Route path="login" element={<LoginPage />} />
        <Route path="*" element={<PlaceholderPage name="notFound" />} />
      </Route>
    </Routes>
  );
}
