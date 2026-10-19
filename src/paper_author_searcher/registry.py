"""Named factory registries: sources are added by registering a factory, chosen and ordered by configuration."""

from typing import Any, Callable, Dict, Generic, Iterable, List, Tuple, TypeVar

T = TypeVar('T')
Factory = Callable[..., T]


class UnknownComponent(ValueError):
    pass


class Registry(Generic[T]):
    """Maps a stable name to a factory; ``create`` builds the configured components in the configured order."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._factories: Dict[str, Factory[T]] = {}

    def register(self, name: str) -> Callable[[Factory[T]], Factory[T]]:
        """Decorator: ``@SOURCES.register('openalex')`` on a factory ``(container, *args) -> component``."""

        def decorate(factory: Factory[T]) -> Factory[T]:
            if name in self._factories:
                raise ValueError(f'{self.kind} {name!r} is already registered')
            self._factories[name] = factory
            return factory

        return decorate

    def names(self) -> Tuple[str, ...]:
        return tuple(self._factories)

    def __contains__(self, name: object) -> bool:
        return name in self._factories

    def factory(self, name: str) -> Factory[T]:
        try:
            return self._factories[name]
        except KeyError:
            raise UnknownComponent(f'Unknown {self.kind} {name!r}; available: {", ".join(self.names())}') from None

    def create(self, names: Iterable[str], *args: Any) -> List[T]:
        names = list(names)
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f'{self.kind} configured more than once: {duplicates}')
        factories = [self.factory(name) for name in names]  # validate every name before building anything
        return [factory(*args) for factory in factories]
