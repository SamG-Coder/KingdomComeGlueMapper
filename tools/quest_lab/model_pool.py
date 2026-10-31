"""Share identical read-only behavior documents across controller audit models."""


class ModelPool:
    def __init__(self):
        self.documents = {}
        self.reused = 0

    def add(self, model):
        # Preserve per-quest declarations, registrations and bindings. A path
        # match alone is insufficient: patched/owner-specific variants differ.
        for path, document in model['behavior_documents'].items():
            variants = self.documents.setdefault(path, [])
            match = next((old for old in variants if old == document), None)
            if match is None:
                variants.append(document)
            else:
                model['behavior_documents'][path] = match
                self.reused += 1
        return model
