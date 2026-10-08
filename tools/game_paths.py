"""Route legacy conversion stages to separate retail installs and a private build."""
import json
from pathlib import Path, PurePosixPath


class GameLibrary:
    def __init__(self, value):
        self.root = Path(value).resolve()
        self.roots = None
        if self.root.is_file():
            config = json.loads(self.root.read_text(encoding='utf-8'))
            if config.get('schema') != 1:
                raise ValueError('Unsupported conversion paths file')
            self.roots = {name: Path(config[key]).resolve() for name, key in (
                ('KingdomComeDeliverance', 'kcd1'), ('KingdomComeDeliverance2', 'kcd2'),
                ('KCD2Mod', 'build'))}
            build = self.roots['KCD2Mod']
            for name in ('KingdomComeDeliverance', 'KingdomComeDeliverance2'):
                game = self.roots[name]
                if build.is_relative_to(game) or game.is_relative_to(build):
                    raise ValueError('Conversion workspace must be separate from game installations')

    def __truediv__(self, relative):
        parts = PurePosixPath(str(relative).replace('\\', '/')).parts
        if not parts or any(p in ('..', '/') or ':' in p for p in parts):
            raise ValueError('Unsafe library path')
        if self.roots is None:
            return self.root.joinpath(*parts)
        if parts[0] not in self.roots:
            raise ValueError('Unknown game root: ' + parts[0])
        return self.roots[parts[0]].joinpath(*parts[1:])

