import { useState, useEffect, useCallback } from "react";
import { X, Code2, Pencil, MessageSquare, Loader2, GraduationCap } from "lucide-react";
import api from "../../../axiosConfig";

const ABILITIES = [
  { key: "can_code", label: "Code", Icon: Code2 },
  { key: "can_draw", label: "Draw", Icon: Pencil },
  { key: "can_chat", label: "Chat", Icon: MessageSquare },
];

// The teacher's switchboard for a lesson in progress. It lives in the IDE
// rather than on the groups page because that is where the teacher is standing
// when a class needs to stop typing and look at the board.
export const ClassPanelModal = ({ isOpen, onClose, groupId }) => {
  const [members, setMembers] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await api.get(`/groups/${groupId}/permissions/`);
      setMembers(res.data.members);
      setError("");
    } catch {
      setError("Could not load the class.");
    } finally {
      setIsLoading(false);
    }
  }, [groupId]);

  useEffect(() => {
    if (isOpen) load();
  }, [isOpen, load]);

  // Leaving out user_id means the whole class, which is the common case: the
  // lesson moves between everyone writing and everyone watching.
  const setAbility = async (key, value, userId) => {
    setIsSaving(true);
    try {
      const body = { [key]: value };
      if (userId !== undefined) body.user_id = userId;
      const res = await api.put(`/groups/${groupId}/permissions/`, body);
      setMembers(res.data.members);
      setError("");
    } catch {
      setError("That change did not go through.");
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  const allHave = (key) => members.length > 0 && members.every((m) => m[key]);

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-[#0B0F17] border border-gray-700 rounded-xl shadow-2xl w-full max-w-lg max-h-[80vh] flex flex-col overflow-hidden">

        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 bg-gray-900/50 flex-shrink-0">
          <h3 className="text-lg font-bold text-white flex items-center gap-2">
            <GraduationCap className="h-5 w-5 text-blue-400" />
            Class controls
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white transition-colors">
            <X className="h-5 w-5" />
          </button>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="h-8 w-8 text-blue-500 animate-spin" />
          </div>
        ) : members.length === 0 ? (
          <div className="p-8 text-center text-gray-400 text-sm">
            Nobody has joined this class yet.
          </div>
        ) : (
          <div className="overflow-y-auto custom-scrollbar">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900 z-10">
                <tr className="text-gray-400 text-xs uppercase tracking-wider">
                  <th className="text-left font-semibold px-6 py-3">Pupil</th>
                  {ABILITIES.map((ability) => (
                    <th key={ability.key} className="font-semibold px-2 py-3">
                      <div className="flex flex-col items-center gap-1">
                        <ability.Icon className="h-4 w-4" />
                        <span>{ability.label}</span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                <tr className="bg-blue-500/5">
                  <td className="px-6 py-3 font-bold text-white">Everyone</td>
                  {ABILITIES.map(({ key }) => (
                    <td key={key} className="px-2 py-3 text-center">
                      <Toggle
                        on={allHave(key)}
                        disabled={isSaving}
                        onClick={() => setAbility(key, !allHave(key))}
                      />
                    </td>
                  ))}
                </tr>

                {members.map((member) => (
                  <tr key={member.id} className="hover:bg-gray-800/40">
                    <td className="px-6 py-3 text-gray-200 truncate max-w-[180px]">
                      {member.name}
                    </td>
                    {ABILITIES.map(({ key }) => (
                      <td key={key} className="px-2 py-3 text-center">
                        <Toggle
                          on={member[key]}
                          disabled={isSaving}
                          onClick={() => setAbility(key, !member[key], member.id)}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="px-6 py-3 border-t border-gray-800 bg-gray-900/50 flex-shrink-0">
          {error ? (
            <p className="text-xs text-red-400">{error}</p>
          ) : (
            <p className="text-xs text-gray-500">
              Changes reach everyone straight away, in every project of this class.
            </p>
          )}
        </div>
      </div>
    </div>
  );
};

const Toggle = ({ on, disabled, onClick }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    aria-pressed={on}
    className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
      on ? "bg-green-600" : "bg-gray-600"
    }`}
  >
    <span
      className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
        on ? "translate-x-6" : "translate-x-1"
      }`}
    />
  </button>
);
