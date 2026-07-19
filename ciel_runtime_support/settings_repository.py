from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository


SettingsFileEffects = SecureJsonEffects
JsonSettingsRepository = SecureJsonRepository


__all__ = ["JsonSettingsRepository", "SettingsFileEffects"]
