import { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, Loader2 } from "lucide-react";
import api from "../../axiosConfig";
import { initializeAuth } from "../components/auth.js";

// The one page in the app written for the children rather than the teacher, so
// the wording is Spanish and there is exactly one thing to do on it.
export default function JoinClass() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // The code rides in the link the teacher hands out. Typing it is only the
  // fallback for a child who reached this page without it.
  const codeFromLink = (searchParams.get("codigo") || "").trim().toUpperCase();

  const [name, setName] = useState("");
  const [typedCode, setTypedCode] = useState("");
  const [error, setError] = useState("");
  const [isJoining, setIsJoining] = useState(false);

  // Somebody who already entered and then reopened the link should not become
  // "Juan 2" just for reloading a page.
  const [existingGuest, setExistingGuest] = useState(null);
  const [isChecking, setIsChecking] = useState(true);

  useEffect(() => {
    document.title = "Entrar a la clase - PyTogether";
  }, []);

  useEffect(() => {
    let cancelled = false;

    const checkExistingSession = async () => {
      try {
        if (await initializeAuth()) {
          const res = await api.get("/api/me/");
          if (!cancelled && res.data?.is_guest) setExistingGuest(res.data.name);
        }
      } catch {
        // No usable session, which is the normal case here.
      } finally {
        if (!cancelled) setIsChecking(false);
      }
    };

    checkExistingSession();
    return () => { cancelled = true; };
  }, []);

  const join = async (e) => {
    e.preventDefault();
    setError("");

    const accessCode = codeFromLink || typedCode.trim().toUpperCase();
    if (!name.trim()) {
      setError("Escribe tu nombre para entrar.");
      return;
    }
    if (!accessCode) {
      setError("Pide a tu profesor el código de la clase.");
      return;
    }

    setIsJoining(true);
    try {
      const res = await api.post(
        "/api/join-class/",
        { access_code: accessCode, name: name.trim() },
        { withCredentials: true }
      );

      sessionStorage.setItem("access_token", res.data.access);
      localStorage.removeItem("previousProjectData");
      navigate("/home");
    } catch (err) {
      const httpStatus = err.response?.status;
      if (httpStatus === 404) setError("Ese código no es de ninguna clase. Revísalo con tu profesor.");
      else if (httpStatus === 400) setError("Escribe tu nombre para entrar.");
      else if (httpStatus === 429) setError("Muchos entrando a la vez. Espera unos segundos y vuelve a intentarlo.");
      else setError("No se pudo entrar. Inténtalo otra vez.");
    } finally {
      setIsJoining(false);
    }
  };

  const continueAsGuest = () => {
    navigate("/home");
  };

  const startOver = () => {
    sessionStorage.removeItem("access_token");
    setExistingGuest(null);
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-gray-950">
      <div className="w-full max-w-md bg-gray-800/60 backdrop-blur-xl border border-gray-700/50 rounded-2xl shadow-2xl overflow-hidden">

        <div className="p-8 text-center border-b border-gray-700/50 bg-gray-800/50">
          <div className="flex justify-center mb-5">
            <div className="rounded-2xl border-2 border-gray-400/50 overflow-hidden shadow-lg">
              <img src="/pytog.png" alt="PyTogether" className="h-20 w-20 object-cover" />
            </div>
          </div>
          <h1 className="text-3xl font-bold text-white mb-2">Entrar a la clase</h1>
          <p className="text-gray-300">Escribe tu nombre y entra. No hace falta nada más.</p>
        </div>

        <div className="p-8">
          {isChecking ? (
            <div className="flex justify-center py-6">
              <Loader2 className="h-8 w-8 text-blue-500 animate-spin" />
            </div>
          ) : existingGuest ? (
            <div className="space-y-4">
              <p className="text-center text-gray-300">
                Ya entraste como <span className="font-bold text-white">{existingGuest}</span>.
              </p>
              <button
                onClick={continueAsGuest}
                className="w-full py-3.5 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl transition-colors flex items-center justify-center gap-2"
              >
                Continuar <ArrowRight className="h-5 w-5" />
              </button>
              <button
                onClick={startOver}
                className="w-full py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
              >
                Entrar con otro nombre
              </button>
            </div>
          ) : (
            <form onSubmit={join} className="space-y-5">
              <div>
                <label htmlFor="nombre" className="block text-sm font-medium text-gray-300 mb-2">
                  Tu nombre
                </label>
                <input
                  id="nombre"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Por ejemplo: Juan"
                  maxLength={24}
                  autoFocus
                  autoComplete="off"
                  className="w-full px-4 py-3.5 text-lg bg-gray-700/40 border border-gray-600/30 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 transition-all"
                  disabled={isJoining}
                />
              </div>

              {!codeFromLink && (
                <div>
                  <label htmlFor="codigo" className="block text-sm font-medium text-gray-300 mb-2">
                    Código de la clase
                  </label>
                  <input
                    id="codigo"
                    value={typedCode}
                    onChange={(e) => setTypedCode(e.target.value.toUpperCase())}
                    placeholder="ABC123"
                    maxLength={20}
                    autoComplete="off"
                    className="w-full px-4 py-3.5 text-lg font-mono tracking-widest text-center bg-gray-700/40 border border-gray-600/30 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 transition-all"
                    disabled={isJoining}
                  />
                </div>
              )}

              {error && <p className="text-red-400 text-sm text-center">{error}</p>}

              <button
                type="submit"
                disabled={isJoining}
                className="w-full py-3.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-600 disabled:cursor-not-allowed text-white font-bold text-lg rounded-xl transition-colors flex items-center justify-center gap-2"
              >
                {isJoining ? <Loader2 className="h-5 w-5 animate-spin" /> : <>Entrar <ArrowRight className="h-5 w-5" /></>}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
