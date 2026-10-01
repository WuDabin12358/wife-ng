package cn.wudabin.wifeng.mixin;

import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.block.BlockState;
import net.minecraft.client.render.WorldRenderer;
import net.minecraft.util.math.BlockPos;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Avoids model lookups that have no backing renderer in a HeadlessMC process. */
@Mixin(WorldRenderer.class)
public abstract class HeadlessWorldRendererMixin {
    @Inject(method = "render", at = @At("HEAD"), cancellable = true)
    private void wifeNg$skipWorldRendering(CallbackInfo callbackInfo) {
        if (FabricLoader.getInstance().isModLoaded("headlessmc")) {
            callbackInfo.cancel();
        }
    }

    @Inject(method = "scheduleBlockRerenderIfNeeded", at = @At("HEAD"), cancellable = true)
    private void wifeNg$skipHeadlessBlockRerender(BlockPos pos, BlockState oldState,
                                                   BlockState newState, CallbackInfo callback) {
        if (FabricLoader.getInstance().isModLoaded("headlessmc")) {
            callback.cancel();
        }
    }
}
