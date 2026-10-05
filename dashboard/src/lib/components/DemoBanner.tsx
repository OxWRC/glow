import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { demoInfo, demoReset, type DemoSchool } from "../api";
import { createI18n, type Locale } from "../i18n";

export function DemoBanner({ locale }: { locale: Locale }) {
  const i18n = useMemo(() => createI18n(locale), [locale]);
  const t = i18n.t;
  const { logout } = useAuth();
  const navigate = useNavigate();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [schools, setSchools] = useState<DemoSchool[] | null>(null);
  const [infoError, setInfoError] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    demoInfo()
      .then((info) => setSchools(info.schools))
      .catch(() => setInfoError(true));
  }, []);

  async function confirmReset() {
    dialogRef.current?.close();
    try {
      await demoReset();
      logout();
      navigate(`/${locale}/login`);
      setStatus(t("demo.resetDone"));
    } catch {
      setStatus(t("demo.resetFailed"));
    }
  }

  return (
    <aside
      aria-label={t("demo.label")}
      className="bg-amber-100 text-amber-900 border-b border-amber-300 px-4 py-2 text-sm"
    >
      <details>
        <summary className="cursor-pointer">{t("demo.summary")}</summary>
        <h2 className="font-semibold mt-2">{t("demo.adminHeading")}</h2>
        <p>{t("demo.adminHint")}</p>
        <h2 className="font-semibold mt-2">{t("demo.schoolsHeading")}</h2>
        {infoError ? (
          <p>{t("demo.infoFailed")}</p>
        ) : schools === null ? (
          <p>{t("demo.loading")}</p>
        ) : schools.length === 0 ? (
          <p>{t("demo.noSchools")}</p>
        ) : (
          <table className="text-left">
            <thead>
              <tr>
                <th scope="col" className="pr-4">
                  {t("demo.schoolId")}
                </th>
                <th scope="col">{t("demo.schoolName")}</th>
              </tr>
            </thead>
            <tbody>
              {schools.map((s) => (
                <tr key={s.id}>
                  <td className="pr-4">{s.id}</td>
                  <td>{s.name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <button
          type="button"
          className="btn-secondary btn-sm mt-2"
          onClick={() => dialogRef.current?.showModal()}
        >
          {t("demo.reset")}
        </button>
      </details>
      <dialog ref={dialogRef} aria-labelledby="demo-reset-title">
        <h2 id="demo-reset-title">{t("demo.resetTitle")}</h2>
        <p>{t("demo.resetWarning")}</p>
        <button
          type="button"
          className="btn-secondary btn-sm"
          onClick={() => dialogRef.current?.close()}
        >
          {t("demo.cancel")}
        </button>
        <button
          type="button"
          className="btn-primary btn-sm"
          onClick={confirmReset}
        >
          {t("demo.confirmReset")}
        </button>
      </dialog>
      <p role="status" aria-live="polite">
        {status}
      </p>
    </aside>
  );
}
